# Scratch bucket for Ansible's aws_ssm connection plugin: SSM sessions carry no file
# transfer, so the plugin stages modules here and the box fetches them with PRESIGNED URLs
# signed by your laptop's credentials. That's why the EC2 role needs no permission on it.
# Objects are throwaway; they expire after a day.
resource "aws_s3_bucket" "ansible_ssm" {
  bucket        = "ijor-ansible-ssm-048589483919"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "ansible_ssm" {
  bucket                  = aws_s3_bucket.ansible_ssm.id
  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "ansible_ssm" {
  bucket = aws_s3_bucket.ansible_ssm.id
  rule {
    id     = "expire-transfers"
    status = "Enabled"
    filter {}
    expiration {
      days = 1
    }
  }
}
