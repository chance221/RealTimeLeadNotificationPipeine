# ============================================================================
# Central Dead-Letter Queue (DLQ) & SQS Processing Queue
# ============================================================================

# Central Dead-Letter Queue (DLQ)
# Captures failed messages/invocations across:
# 1. SQS Processing Queue (after maxReceiveCount retries by the Ingestion Lambda)
# 2. Scheduler Lambda (asynchronous invocation failures)
# 3. EventBridge Scheduler (failed 10-minute timer target invocations)
# 4. Processing & SES Lambda (failed email/processing invocations)
resource "aws_sqs_queue" "dlq" {
  name                      = "${var.project_name}-${var.environment}-dlq"
  message_retention_seconds = 1209600 # 14 days (maximum SQS retention for root-cause inspection)
  sqs_managed_sse_enabled   = true
}

# Primary Processing Queue
# Receives raw Close CRM webhook payloads directly from API Gateway
# and buffers them for the Ingestion Lambda.
resource "aws_sqs_queue" "processing_queue" {
  name                       = "${var.project_name}-${var.environment}-processing-queue"
  visibility_timeout_seconds = 180    # 6x the 30-second Ingestion Lambda timeout (AWS best practice)
  message_retention_seconds  = 345600 # 4 days
  receive_wait_time_seconds  = 20     # Enable long polling to reduce empty receives and cost
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

# Explicitly restrict which queues can redrive into the DLQ
resource "aws_sqs_queue_redrive_allow_policy" "dlq_redrive_allow" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.processing_queue.arn]
  })
}
