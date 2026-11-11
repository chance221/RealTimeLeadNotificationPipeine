variable "aws_region" {
  description = "AWS region where all resources will be deployed"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name prefix used for naming AWS resources"
  type        = string
  default     = "close-lead-pipeline"
}

variable "environment" {
  description = "Deployment environment (e.g., dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "git_commit_sha" {
  description = "Immutable Git commit SHA used to version the artifact folder path in S3"
  type        = string
  default     = "local"
}

variable "artifact_bucket_name" {
  description = "Name of the S3 bucket storing Terraform state and packaged Lambda artifacts (must be lowercase for S3)"
  type        = string
  default     = "real-time-close-api-scripts"

  validation {
    condition     = lower(var.artifact_bucket_name) == var.artifact_bucket_name
    error_message = "AWS S3 bucket names must be all lowercase (e.g., 'realtimecloseapiscripts')."
  }
}

variable "data_lake_bucket_name" {
  description = "Name of the S3 bucket storing in-process and processed lead payloads (must be lowercase for S3)"
  type        = string
  default     = "real-time-close-api-messages"

  validation {
    condition     = lower(var.data_lake_bucket_name) == var.data_lake_bucket_name
    error_message = "AWS S3 bucket names must be all lowercase (e.g., 'realtimecloseapimessages')."
  }
}
