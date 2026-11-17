output "instance_id" {
  value = one(aws_instance.server[*].id)
}

# Open the app: run this (needs session-manager-plugin), then browse http://localhost:8000
output "ssm_port_forward_command" {
  value = var.app_running ? "aws ssm start-session --target ${aws_instance.server[0].id} --document-name AWS-StartPortForwardingSession --parameters portNumber=80,localPortNumber=8000 --profile gt" : "server is off - terraform apply -var app_running=true"
}

output "ansible_ssm_bucket" {
  value = aws_s3_bucket.ansible_ssm.bucket
}
