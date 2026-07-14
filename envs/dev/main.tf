provider "aws" {
  region = var.aws_region
}

module "app" {
  source = "../../modules/app-stack"

  project         = var.project
  environment     = "dev"
  aws_region      = var.aws_region
  deployed_commit = var.deployed_commit
}
