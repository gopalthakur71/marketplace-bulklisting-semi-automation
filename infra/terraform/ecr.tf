# Image registry the CI build-and-push job writes to and the EC2 box pulls :latest from.
resource "aws_ecr_repository" "app" {
  name                 = "marketplace-bulklisting"
  image_tag_mutability = "MUTABLE" # :latest is re-pointed on every push to main

  encryption_configuration {
    encryption_type = "AES256"
  }
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      # 3 = :latest plus two to roll back to. Older builds are never needed (2026-11-17).
      description = "Keep only the 3 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}
