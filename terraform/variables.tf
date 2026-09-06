variable "project_name" {
  type    = string
  default = "ai-chatbot"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.project_name))
    error_message = "Use up to 20 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Environment must be dev or prod."
  }
}

variable "log_level" {
  type = string
  validation {
    condition     = contains(["DEBUG", "INFO", "WARN", "ERROR"], var.log_level)
    error_message = "Use DEBUG, INFO, WARN or ERROR."
  }
}

variable "worker_timeout" {
  type    = number
  default = 120
  validation {
    condition     = var.worker_timeout >= 120 && var.worker_timeout <= 900 && floor(var.worker_timeout) == var.worker_timeout
    error_message = "Worker timeout must be an integer from 120 to 900 seconds."
  }
}

variable "worker_concurrency" {
  type    = number
  default = 5
  validation {
    condition     = var.worker_concurrency >= 2 && var.worker_concurrency <= 1000 && floor(var.worker_concurrency) == var.worker_concurrency
    error_message = "Worker concurrency must be an integer from 2 to 1000."
  }
}

variable "api_rate_limit" {
  type    = number
  default = 10
}

variable "api_burst_limit" {
  type    = number
  default = 20
}

# PR10 selects the model and supplies the exact model/profile resource ARNs.
variable "bedrock_resource_arns" {
  type    = set(string)
  default = []
  validation {
    condition     = alltrue([for arn in var.bedrock_resource_arns : can(regex("^arn:[^:]+:bedrock:[^:]+:[0-9]*:(foundation-model|inference-profile|application-inference-profile)/[^*?]+$", arn))])
    error_message = "Supply exact Bedrock model or inference profile ARNs without wildcards."
  }
}

variable "point_in_time_recovery_enabled" {
  type    = bool
  default = true
}
