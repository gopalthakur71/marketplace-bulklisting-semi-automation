# Adopted 2026-11-17. At import it also had 0.0.0.0/0 on 22 and 80 plus two stale laptop
# IPs; those were removed right after adoption (nothing on the box needs inbound: CI deploys
# and the SSM agent only make outbound connections).
resource "aws_security_group" "app" {
  name        = "listing-app-sg"
  description = "Marigold Ops web app"
  vpc_id      = "vpc-034e1efd5a11da069"

  # ZERO inbound (phase 4, pulled forward with phase 5): the app has no login, so the network
  # must have no path in. Access is the SSM port forward only (the agent connects outbound).
  # Never add an ingress rule here, least of all 0.0.0.0/0.
  # `ingress = []` is required: with no ingress blocks at all, Terraform stops managing inbound
  # rules and silently leaves the existing ones in place. The empty list makes it delete them.
  ingress = []

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}
