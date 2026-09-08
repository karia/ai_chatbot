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

variable "ingress_timeout" {
  description = "Execution ceiling in seconds for completing delayed SQS delivery, independent of Slack's acknowledgement deadline. Must exceed the API integration timeout."
  type        = number
  default     = 10
  validation {
    condition     = var.ingress_timeout > 5 && var.ingress_timeout <= 900 && floor(var.ingress_timeout) == var.ingress_timeout
    error_message = "Ingress timeout must be an integer from 6 to 900 seconds, exceeding the 5-second API integration timeout."
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

variable "slack_team_id" {
  description = "Slack workspace allowed to submit events. Supply per environment."
  type        = string
  validation {
    condition     = can(regex("^T[A-Z0-9]+$", var.slack_team_id))
    error_message = "Supply a Slack workspace ID."
  }
}

variable "slack_api_app_id" {
  description = "Slack app allowed to submit events. Supply per environment."
  type        = string
  validation {
    condition     = can(regex("^A[A-Z0-9]+$", var.slack_api_app_id))
    error_message = "Supply a Slack app ID."
  }
}
