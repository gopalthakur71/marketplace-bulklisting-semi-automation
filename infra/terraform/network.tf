# Your laptop's public IP. The ISP changes it from time to time: when the SSH tunnel stops
# connecting, check `curl https://checkip.amazonaws.com`, update this, plan + apply.
# Phase 4 deletes both ingress rules (access moves to SSM port forwarding, no IP needed).
locals {
  laptop_cidr = "223.190.87.103/32"
}

# Adopted 2026-11-17. At import it also had 0.0.0.0/0 on 22 and 80 plus two stale laptop
# IPs; those were removed right after adoption (nothing on the box needs inbound: CI deploys
# and the SSM agent only make outbound connections).
resource "aws_security_group" "app" {
  name        = "listing-app-sg"
  description = "Marigold Ops web app"
  vpc_id      = "vpc-034e1efd5a11da069"

  ingress {
    description = "laptop-http"
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = [local.laptop_cidr]
  }
  ingress {
    description = "laptop-ssh"
    protocol    = "tcp"
    from_port   = 22
    to_port     = 22
    cidr_blocks = [local.laptop_cidr]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}
