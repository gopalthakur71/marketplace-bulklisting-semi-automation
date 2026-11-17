provider "aws" {
  region  = "ap-south-1"
  profile = "gt"

  default_tags {
    tags = {
      ManagedBy = "terraform"
      Project   = "listing-app"
    }
  }
}
