# ============================================================================
# Step 5: Processing & SES Notification Lambda Function
# Invoked by EventBridge Scheduler 10 minutes after a lead JSON lands in S3.
# Reads the lead from `in-process/`, emails the notification via Amazon SES,
# copies the object to `processed/`, and removes it from `in-process/`.
# ============================================================================

variable "ses_sender_email" {
  description = "Verified Amazon SES sender email address (From)"
  type        = string
  default     = "intelectual221@gmail.com"
}

variable "ses_recipient_email" {
  description = "Verified Amazon SES recipient email address (To)"
  type        = string
  default     = "intelectual221@gmail.com"
}

variable "lead_owner_base_url" {
  description = "Base URL of the public S3 bucket containing {lead_id}.json lead owner lookup files"
  type        = string
  default     = "https://dea-lead-owner.s3.us-east-1.amazonaws.com"
}

# Register `intelectual221@gmail.com` in Amazon SES (triggers an AWS verification email to your inbox)
resource "aws_ses_email_identity" "lead_notification_email" {
  email = var.ses_recipient_email
}

resource "aws_cloudwatch_log_group" "processing_lambda" {
  name              = "/aws/lambda/${var.project_name}-${var.environment}-processing"
  retention_in_days = 14
}

resource "aws_iam_role" "processing_lambda_role" {
  name = "${var.project_name}-${var.environment}-processing-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# Least-Privilege IAM Policy for the Processing & SES Lambda:
# 1. Read and delete lead JSON objects from `in-process/*`
# 2. Write processed lead JSON objects into `processed/*`
# 3. Send lead notification emails via Amazon SES (`ses:SendEmail`, `ses:SendRawEmail`)
# 4. Send failed invocation events to the central Dead-Letter Queue (DLQ)
# 5. Write execution logs to CloudWatch Logs
resource "aws_iam_role_policy" "processing_lambda_policy" {
  name = "${var.project_name}-${var.environment}-processing-lambda-policy"
  role = aws_iam_role.processing_lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadAndRemoveLeadsFromInProcessPrefix"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:GetObjectVersion",
          "s3:DeleteObject",
          "s3:GetObjectTagging"
        ]
        Resource = "${aws_s3_bucket.data_lake.arn}/in-process/*"
      },
      {
        Sid      = "ListDataLakeBucketForHeadObjectChecks"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.data_lake.arn
      },
      {
        Sid    = "WriteLeadsToProcessedPrefix"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:PutObjectTagging"
        ]
        Resource = "${aws_s3_bucket.data_lake.arn}/processed/*"
      },
      {
        Sid    = "SendLeadNotificationEmailViaSES"
        Effect = "Allow"
        Action = [
          "ses:SendEmail",
          "ses:SendRawEmail"
        ]
        Resource = "*"
      },
      {
        Sid      = "SendFailureNotificationsToDLQ"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.dlq.arn
      },
      {
        Sid    = "WriteCloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.processing_lambda.arn}:*"
      }
    ]
  })
}

resource "aws_lambda_function" "processing" {
  function_name = "${var.project_name}-${var.environment}-processing"
  description   = "Invoked 10 minutes after lead ingestion; sends SES email notification and moves lead from in-process/ to processed/"
  role          = aws_iam_role.processing_lambda_role.arn

  s3_bucket         = aws_s3_bucket.artifacts.id
  s3_key            = aws_s3_object.processing_lambda_zip.key
  s3_object_version = aws_s3_object.processing_lambda_zip.version_id
  source_code_hash  = data.archive_file.processing_zip.output_base64sha256

  runtime     = "python3.12"
  handler     = "handler.lambda_handler"
  timeout     = 30
  memory_size = 256

  dead_letter_config {
    target_arn = aws_sqs_queue.dlq.arn
  }

  environment {
    variables = {
      DATA_LAKE_BUCKET_NAME = aws_s3_bucket.data_lake.id
      IN_PROCESS_PREFIX     = "in-process"
      PROCESSED_PREFIX      = "processed"
      LEAD_OWNER_BASE_URL   = var.lead_owner_base_url
      SES_SENDER_EMAIL      = var.ses_sender_email
      SES_RECIPIENT_EMAIL   = var.ses_recipient_email
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.processing_lambda,
    aws_iam_role_policy.processing_lambda_policy
  ]
}

resource "aws_lambda_function_event_invoke_config" "processing_async_dlq" {
  function_name          = aws_lambda_function.processing.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.dlq.arn
    }
  }
}
