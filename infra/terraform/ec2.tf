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

# SPOT ONLY, ANY SIMILAR TYPE. Instead of naming one instance type (and falling back to
# paying on-demand when it has no Spot capacity), the box is launched by an "instant" EC2
# Fleet that describes the SHAPE we need and lets AWS pick the cheapest Spot type that has
# capacity right now (price-capacity-optimized). With the AL2023 x86_64 AMI this matches
# t3.small / t3a.small / t3.medium / t3a.medium / t2.medium — any of them runs the app.
# If AWS reclaims the box mid-session: re-run apply + ansible-playbook (data is all in S3).
locals {
  server_vcpus          = 2    # same as t3.small
  server_memory_min_mib = 2048 # t3.small's 2 GB is the floor (the image pull needs it)
  server_memory_max_mib = 4096 # allow a medium if it's the cheapest Spot at that moment
}

# Latest Amazon Linux 2023, resolved from AWS's public SSM parameter.
data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# Everything about the box except its type. Free to keep around while the server is off.
resource "aws_launch_template" "server" {
  name                   = "listing-app"
  image_id               = data.aws_ssm_parameter.al2023_ami.value
  update_default_version = true

  iam_instance_profile {
    name = aws_iam_instance_profile.ec2.name
  }

  network_interfaces {
    subnet_id                   = "subnet-017153e0ff17bc723"
    security_groups             = [aws_security_group.app.id]
    associate_public_ip_address = true # outbound to SSM/ECR/S3 (no VPC endpoints, no NAT)
    delete_on_termination       = true
  }

  credit_specification {
    cpu_credits = "standard" # throttle instead of billing extra on sustained CPU
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2          # lets the container reach the instance role
  }

  # 8 GB is the smallest disk the AL2023 AMI allows (its snapshot is 8 GB); ~$0.73/mo
  # pro-rata, and only while the box exists — the volume is deleted with it.
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 8
      encrypted             = true
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "listing-app" } # the CI deploy job + Ansible inventory find it by this
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "listing-app" }
  }
}

resource "aws_ec2_fleet" "server" {
  count = var.app_running ? 1 : 0

  type                = "instant" # one-shot launch: no re-launch behind your back
  terminate_instances = true      # deleting the fleet (app_running=false) deletes the box

  launch_template_config {
    launch_template_specification {
      launch_template_id = aws_launch_template.server.id
      version            = aws_launch_template.server.latest_version
    }
    override {
      instance_requirements {
        vcpu_count {
          min = local.server_vcpus
          max = local.server_vcpus
        }
        memory_mib {
          min = local.server_memory_min_mib
          max = local.server_memory_max_mib
        }
        burstable_performance = "required" # t-family only (cheapest, and `standard` credits apply)
      }
    }
  }

  target_capacity_specification {
    default_target_capacity_type = "spot" # never on-demand
    total_target_capacity        = 1
  }

  spot_options {
    allocation_strategy            = "price-capacity-optimized"
    instance_interruption_behavior = "terminate" # nothing on the box to keep
    # Both already true here (one subnet = one AZ, one box = one type). Stated explicitly
    # because the provider always sends MinTargetCapacity, which AWS rejects otherwise
    # ("only available with single instance fleets or single AZ fleets").
    single_availability_zone = true
    single_instance_type     = true
    # no max price: pay the current Spot price, capped at the on-demand price
  }

  lifecycle {
    # A new AL2023 release (new launch-template version) must not replace a running box
    # mid-session; the next off→on cycle picks it up anyway.
    ignore_changes = [launch_template_config]
  }
}

locals {
  # Empty when the server is off, or when no Spot capacity was found for any matching type.
  server_instance_id = try(one(aws_ec2_fleet.server[0].fleet_instance_set[0].instance_ids), null)
}
