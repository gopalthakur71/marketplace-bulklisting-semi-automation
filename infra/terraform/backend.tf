# State lives in a bucket created once by hand (see infra/bootstrap-state-bucket.sh) —
# Terraform can't create the bucket that holds its own state.
# use_lockfile = S3-native locking (a .tflock object), so no DynamoDB table is needed.
terraform {
  backend "s3" {
    bucket       = "ijor-tfstate-048589483919"
    key          = "listing-app/terraform.tfstate"
    region       = "ap-south-1"
    profile      = "gt"
    encrypt      = true
    use_lockfile = true
  }
}
