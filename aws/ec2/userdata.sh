#!/bin/bash
set -euo pipefail

# ── Marigold Ops web app — EC2 user-data (Stage 1: AUTH_DISABLED=1) ──
# Installs Docker and a systemd unit that pulls :latest from ECR on every
# boot (boot = deploy). Upload this file directly in the EC2 console's
# User data → "Choose file" so no CRLF/paste mangling can occur.

dnf install -y docker
systemctl enable --now docker

ACCOUNT=048589483919
REGION=ap-south-1
IMAGE=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/marketplace-bulklisting:latest

cat >/etc/systemd/system/listing-app.service <<UNIT
[Unit]
Description=Marketplace Listing App
After=docker.service
Requires=docker.service

[Service]
ExecStartPre=-/usr/bin/docker rm -f listing-app
ExecStartPre=/bin/bash -lc 'aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com'
ExecStartPre=/usr/bin/docker pull $IMAGE
ExecStart=/usr/bin/docker run --rm --name listing-app -p 80:8080 \
  -e AUTH_DISABLED=1 \
  -e AWS_REGION=$REGION \
  $IMAGE
ExecStop=/usr/bin/docker stop listing-app
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now listing-app.service
