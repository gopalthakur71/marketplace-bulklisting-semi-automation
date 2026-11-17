provider "aws" {
  region  = "ap-south-1"
  profile = "gt"

  # default_tags deliberately omitted during the phase-2 imports: they would put a tag
  # diff on every adopted resource, hiding the "No changes" signal. Add after adoption.
}
