# my-personal-coach

Project Name: kAIzen Coach
Domain: kaizencoach.training
Repository: https://github.com/smurphin/my-personal-coach

Architecture docs here https://github.com/smurphin/my-personal-coach/docs/architecture 

1.1. Project Overview
kAIzen Coach is a personalized, AI-powered endurance coaching application. It is designed to provide athletes with adaptive training plans and data-driven feedback by leveraging their real-world performance data from Strava. The application uses Google's Gemini AI model to act as an elite coach, creating bespoke training schedules based on modern, evidence-based methodologies like polarized training (80/20), Joe Friel's heart rate zones, and Dr. Jack Daniels' VDOT paces.

The core user journey involves:

Authentication: Securely connecting a user's Strava account via OAuth2.

Onboarding: Capturing the user's primary goal, training availability, and athletic profile through a simple web form.

Plan Generation: Analyzing the user's recent (last 60 activities) and long-term Strava data to generate a personalized training plan.

Adaptive Feedback Loop: Analyzing the user's most recent activity against their current plan and providing feedback. The AI reviews the entire feedback history for trends and can automatically suggest and apply updates to the plan.

Long-Term Memory: Summarizing completed training cycles to provide historical context for future plans, ensuring continuous and intelligent progression.

1.2. Technical Architecture
The project is organized within a single monorepo, separating the application code from the infrastructure-as-code.

Application (Repository Root):

Framework: A Python Flask web application serves as the core backend.

AI Integration: Uses the Google Cloud Vertex AI library to interact with the Gemini large language model.

Data Handling: A data_manager.py module acts as a repository layer, intelligently switching between a local JSON file for development and a DynamoDB backend for production.

Containerization: A multi-stage Dockerfile builds a lean, production-ready container image using gunicorn as the web server.

Infrastructure (infra/ directory):

Provisioning: All cloud resources are managed via Terraform, enabling a fully automated, repeatable deployment process.

Hosting: The application container is hosted on AWS App Runner, a serverless container service that handles scaling and load balancing.

Database: User data is stored in Amazon DynamoDB, a serverless NoSQL database.

Secrets: All sensitive credentials are securely stored in AWS Secrets Manager.

DNS: The custom domain is managed by AWS Route 53, with Terraform automatically creating all necessary records.
