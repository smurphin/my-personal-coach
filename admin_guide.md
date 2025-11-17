cd ~/git/my-personal-coach

# 1. Make sure s3_manager.py is in the repo root
ls -lh s3_manager.py app.py

# 2. Build the Docker image
sudo docker build -t 321490400104.dkr.ecr.eu-west-1.amazonaws.com/my-personal-coach-app:latest .

# 3. Push to ECR
sudo docker push 321490400104.dkr.ecr.eu-west-1.amazonaws.com/my-personal-coach-app:latest

# 4. App Runner will auto-deploy the new image
# Watch logs
aws logs tail /aws/apprunner/my-personal-coach-service/service --follow