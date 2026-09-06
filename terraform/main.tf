terraform {
  required_version = ">= 1.15"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.18.0, < 7.0.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
  }
  backend "s3" {
    key          = "ai-chatbot-memory-verification/terraform.tfstate"
    region       = "ap-northeast-1"
    encrypt      = true
    use_lockfile = true
  }
}
provider "aws" {
  region = "ap-northeast-1"
}

resource "aws_bedrockagentcore_memory" "verification" {
  name                  = "AiChatbotMemoryVerification"
  description           = "Temporary session persistence verification"
  event_expiry_duration = 30
}
