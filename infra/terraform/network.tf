# Imported exactly as found in the console (2026-11-17). Phase 4 removes every ingress rule
# (access moves to SSM port forwarding). NOTE: the 0.0.0.0/0 rules on 22 and 80 were found
# at import time and contradict docs/infra-resources.md ("My IP only").
resource "aws_security_group" "app" {
  name        = "listing-app-sg"
  description = "Marigold Ops web app"
  vpc_id      = "vpc-034e1efd5a11da069"

  ingress {
    protocol    = "tcp"
    from_port   = 22
    to_port     = 22
    cidr_blocks = ["0.0.0.0/0", "223.190.80.43/32"]
  }
  ingress {
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    description = "laptop-http"
    protocol    = "tcp"
    from_port   = 80
    to_port     = 80
    cidr_blocks = ["223.190.84.89/32"]
  }
  ingress {
    description = "laptop-ssh"
    protocol    = "tcp"
    from_port   = 22
    to_port     = 22
    cidr_blocks = ["223.190.84.89/32"]
  }

  egress {
    protocol    = "-1"
    from_port   = 0
    to_port     = 0
    cidr_blocks = ["0.0.0.0/0"]
  }
}
