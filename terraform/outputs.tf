output "artifact_bucket_name" {
  description = "Name of the S3 artifact bucket created by Terraform"
  value       = aws_s3_bucket.artifacts.id
}

output "artifact_bucket_arn" {
  description = "ARN of the S3 artifact bucket created by Terraform"
  value       = aws_s3_bucket.artifacts.arn
}

output "data_lake_bucket_name" {
  description = "Name of the S3 data lake bucket for lead payloads"
  value       = aws_s3_bucket.data_lake.id
}

output "data_lake_bucket_arn" {
  description = "ARN of the S3 data lake bucket for lead payloads"
  value       = aws_s3_bucket.data_lake.arn
}

output "dlq_url" {
  description = "URL of the central SQS Dead-Letter Queue"
  value       = aws_sqs_queue.dlq.id
}

output "dlq_arn" {
  description = "ARN of the central SQS Dead-Letter Queue"
  value       = aws_sqs_queue.dlq.arn
}

output "processing_queue_url" {
  description = "URL of the SQS Processing Queue"
  value       = aws_sqs_queue.processing_queue.id
}

output "close_webhook_endpoint_url" {
  description = "Invoke URL for the POST /webhooks/close endpoint to register in Close CRM"
  value       = "${aws_api_gateway_stage.webhook_stage.invoke_url}/webhooks/close"
}

output "ingestion_lambda_name" {
  description = "Name of the SQS-polling Ingestion Lambda function"
  value       = aws_lambda_function.ingestion.function_name
}

output "ingestion_lambda_arn" {
  description = "ARN of the SQS-polling Ingestion Lambda function"
  value       = aws_lambda_function.ingestion.arn
}

output "scheduler_lambda_name" {
  description = "Name of the S3-triggered Scheduler Lambda function"
  value       = aws_lambda_function.scheduler.function_name
}

output "processing_lambda_name" {
  description = "Name of the EventBridge Scheduler-triggered Processing & SES Lambda function"
  value       = aws_lambda_function.processing.function_name
}

output "eventbridge_schedule_group_name" {
  description = "Name of the EventBridge Scheduler group holding 10-minute lead timers"
  value       = aws_scheduler_schedule_group.lead_timers.name
}
