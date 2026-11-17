# The CURRENT box (console-built). Adopted so its config is visible in code; phase 3 replaces
# it with a fresh instance configured by Ansible. Until then, do not change anything here.
resource "aws_instance" "app" {
  ami                    = "ami-0d351f1b760a30161"
  instance_type          = "t3.micro"
  subnet_id              = "subnet-017153e0ff17bc723"
  vpc_security_group_ids = [aws_security_group.app.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2.name
  key_name               = "listing-app"

  credit_specification {
    cpu_credits = "unlimited"
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # lets the container reach the instance role
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    iops        = 3000
    throughput  = 125
  }

  tags = {
    Name = "listing-app" # ⚠️ the CI deploy job targets this tag — exactly one instance may carry it
  }

  lifecycle {
    # user_data is the stale Stage-1 bootstrap (AUTH_DISABLED=1); the real unit on the box was
    # edited by hand since. Changing user_data forces a stop/start, so it is ignored, not managed.
    ignore_changes = [user_data, user_data_base64]
  }
}
