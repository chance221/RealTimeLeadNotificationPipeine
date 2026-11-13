# Real Time Lead Notification Pipeine

!["Architecture Diagram"](images/RealTimeLeadNotificationPipeline.jpg)
</br></br>
Notification Pipeline that forwards received messages from Close API webhook that is ingested using an AWS API Endpoint, transfered to an SQS Queue, then saved to an S3 bucket. This busket is linked to an event trigger that schedules a 30 minuete wait task implemented to wait for lead assignment, then processed after gathering that lead assignment information  stored in a publically available S3 bucket, and an email is sent with all pertenant lead information using AWS SES.

</br></br>
## Notes About the Architecture

### API Gateway

Instead of risking that a http trigger on a Lambda Function hitting concurrency limits and potentially rejecting requests, using AWS API Gateway allows for acceptance of ten thousands messages per second (and higher if needed), better security by having built in throttling and rate limiting, and can require API keys to prevent traffic spikes or malicious bots from overwhelming the Lambda Function.

Lambda Function URL triggers only support public access or AWS IAM authentication while the API Gateway supports many more methods of modern access control such sa AWS Cognito, or JWT authorizers.

</br></br>
### Amazon SQS
By implementing a SQS Queue to sit between the Lambda Function and API post request we allow for automatic polling and scaling of up to 1,000 concurrent executions. If a spike occurs Lambda automatically creates parallel workers that can handle processing up to 10 messages per invocation reducing costs of high-volume processing. If this will overwhelm downstream processing steps you can set the concurrency limits to only send what your downstream systems can handle, reducing the need to increase those downstream resources to handle spikes, while not losing messages throughout. Also if the lambda function stops for any reason a complete history can be obtained and replayed as needed. 
</br></br>
### AWS Lambda
The code is maintained throught a GitHub repository, and maintains immutaiblity through the trust relationships, and the Generated commit hash that is stored with every update of atrifact code, linking each run to a commit in the repository. Queue Messaging and polling , as well as event bridge triggers are used to invoke different Lambda Functions during this notification process.
</br></br>

### S3 Bucket
The processing bucket is linked to an event trigger that  schedule an AWS Event Bridge wait process of 30 minuets to  account for the time it takes from lead generattion to lead assignment before the final processing can occur. A file lookup in an external S3 bucket that holds the assignment occurs and the files once completed move from in-process to processed and an email is sent using AWS SES that holds all pertenant information required to complete the process
</br>
</br>
</br>
## CI/CD Pipeline

### GithHub Actions Workflow
The workflow was used to first run a suite of basic test to ensure no functionality is broken during most recent code push. If the test fails the build fails, and no changes are made to the AWS infrastructure. If successful artifacts are ziped and stored in the correct location for each AWS lambda handler necessary to run the pipeline. 

</br>

### Terraform
Upon successful build Trerraform is completely responsible for creating, and maintaining all AWS resources,this entire environment can be created or destroyed with a command, and any changes made to the the lambda functions, the SQS Queue, the event bridge wait scheduler or the S3 buckets are handeled upon mervge to the production environment linked to the master repository branch in GitHub