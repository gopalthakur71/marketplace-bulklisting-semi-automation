output "instance_id" {
  value = aws_instance.app.id
}

# Phase 4 access path (works once the SSM agent is up; needs session-manager-plugin locally).
output "ssm_port_forward_command" {
  value = "aws ssm start-session --target ${aws_instance.app.id} --document-name AWS-StartPortForwardingSession --parameters portNumber=80,localPortNumber=8000 --profile gt"
}
