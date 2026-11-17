# The app server — DISPOSABLE. It exists only while you're using it; everything that matters
# (images + app state in S3, config in SSM, the image in ECR) lives outside it.
#
#   terraform apply -var app_running=true    # build a fresh box (then: ansible-playbook site.yml)
#   terraform apply -var app_running=false   # delete the box → $0 compute
#
# No default on purpose: Terraform asks every time, so the box is never built or deleted by
# accident. Built bare (no user_data); Ansible (infra/ansible) configures it. No SSH key —
# access is SSM only. While it's off, the CI deploy job fails (no box to restart); the next
# "on" pulls :latest anyway, so no deploy is ever missed.
variable "app_running" {
  type        = bool
  description = "true = the listing-app server exists, false = deleted (costs nothing)"
}

# Latest Amazon Linux 2023, resolved from AWS's public SSM parameter.
data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "server" {
  count = var.app_running ? 1 : 0

  ami                         = data.aws_ssm_parameter.al2023_ami.value
  instance_type               = "t3.micro"
  subnet_id                   = "subnet-017153e0ff17bc723"
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.ec2.name
  associate_public_ip_address = true # outbound to SSM/ECR/S3 (no VPC endpoints, no NAT)

  credit_specification {
    cpu_credits = "standard" # throttle instead of billing extra on sustained CPU
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # lets the container reach the instance role
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 8 # budget: docs/runbooks/ec2-box-configuration.md
    encrypted   = true
  }

  tags = {
    Name = "listing-app" # the CI deploy job targets this tag
  }

  lifecycle {
    # A new AL2023 release must not replace a running box mid-session; the next off→on
    # cycle picks it up anyway.
    ignore_changes = [ami]
  }
}

# Phase-3 cutover: the box built as aws_instance.next (tagged listing-app-next) IS the server.
moved {
  from = aws_instance.next
  to   = aws_instance.server[0]
}
