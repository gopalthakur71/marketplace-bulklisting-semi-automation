output "instance_id" {
  value = local.server_instance_id
}

# Open the app: run this (needs session-manager-plugin), then browse http://localhost:8000
output "ssm_port_forward_command" {
  value = !var.app_running ? "server is off - terraform apply -var app_running=true" : (
    local.server_instance_id == null
    ? "no Spot capacity for any matching type right now - run apply again in a few minutes"
    : "aws ssm start-session --target ${local.server_instance_id} --document-name AWS-StartPortForwardingSession --parameters portNumber=80,localPortNumber=8000 --profile gt"
  )
}

output "ansible_ssm_bucket" {
  value = aws_s3_bucket.ansible_ssm.bucket
}
