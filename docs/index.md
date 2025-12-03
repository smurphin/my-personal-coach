---
layout: default
title: Home
---
# kAIzen Coach Documentation

AI-powered endurance coaching application with multi-environment deployment on AWS and GCP.

<!-- toc -->

- [🏗️ Architecture](#%F0%9F%8F%97%EF%B8%8F-architecture)
- [🚀 Deployment & Setup](#%F0%9F%9A%80-deployment--setup)
- [📊 Current Infrastructure](#%F0%9F%93%8A-current-infrastructure)
  * [Environments](#environments)
  * [Stack](#stack)
  * [Monthly Costs](#monthly-costs)
- [🔗 Quick Links](#%F0%9F%94%97-quick-links)

<!-- tocstop -->

---

## 🏗️ Architecture

Understanding the application structure and data flow:

- **[Application Flow Visualization](architecture/app-flow-visualisation.html)** - Interactive architecture diagram
- **[Architecture Overview](architecture/architecture)** - Detailed architecture documentation

---

## 🚀 Deployment & Setup

Complete guides for deploying and managing environments:

- **[Multi-Environment Setup](multi_env_setup)** - Complete AWS and GCP setup guide
- **[New Environment Deployment](new_env_deployment)** - Step-by-step deployment process
- **[Secrets Management Guide](secrets-guide)** - All secrets explained with generation commands

---

## 📊 Current Infrastructure

### Environments
- **Production**: [www.kaizencoach.training](https://www.kaizencoach.training)
- **Staging**: [staging.kaizencoach.training](https://staging.kaizencoach.training)
- **Dev**: Local development environment

### Stack
- **Frontend**: Flask, Tailwind CSS, Chart.js
- **Backend**: AWS App Runner, DynamoDB, S3
- **AI**: Google Vertex AI (Gemini 2.5 Flash)
- **Integrations**: Strava OAuth2, Garmin Connect
- **Infrastructure**: Terraform, Docker, AWS ECR

### Monthly Costs
- Production: ~$30/month (always-on)
- Staging: ~$8/month (low-cost)
- Dev: ~$0/month (local only)

---

## 🔗 Quick Links

- [GitHub Repository](https://github.com/smurphin/my-personal-coach)
- [Live Application](https://www.kaizencoach.training)
- [Staging Environment](https://staging.kaizencoach.training)

---

<small>Last updated: December 2025 | [Edit on GitHub](https://github.com/smurphin/my-personal-coach/tree/main/docs)</small>
