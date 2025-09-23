

### How to Test the Strava Webhook Setup (for Troubleshooting Doc)

````markdown
If you suspect that webhook events from Strava are not being received, follow these steps to diagnose the issue.

### Step 1: Check Your Active Subscription with Strava

This command asks Strava's API to list all active webhook subscriptions for your application. This confirms that Strava has the correct callback URL on record.

Run the following command in your terminal, substituting your application's `CLIENT_ID` and `CLIENT_SECRET`:

```bash
curl -G [https://www.strava.com/api/v3/push_subscriptions](https://www.strava.com/api/v3/push_subscriptions) \
-d client_id=YOUR_CLIENT_ID \
-d client_secret=YOUR_CLIENT_SECRET
````

**Expected Output:**
You should see a JSON response containing your subscription details. Verify that the `callback_url` is correct.

```json
[
  {
    "id": 305096,
    "callback_url": "[https://www.kaizencoach.training/strava_webhook](https://www.kaizencoach.training/strava_webhook)",
    ...
  }
]
```

  - If the entry exists and the URL is correct, proceed to Step 2.
  - If the entry is missing or the URL is incorrect, you must [create a new subscription](https://www.google.com/search?q=https://developers.strava.com/docs/webhooks/%23subscribe) via the API.

### Step 2: Manually Test Your Live Endpoint

This step verifies that your deployed application is reachable and the verification logic is working correctly.

Run this `curl` command in your terminal, using the `verify_token` you stored in AWS Secrets Manager:

```bash
curl -v "[https://www.kaizencoach.training/strava_webhook?hub.challenge=test_challenge&hub.verify_token=YOUR_VERIFY_TOKEN](https://www.kaizencoach.training/strava_webhook?hub.challenge=test_challenge&hub.verify_token=YOUR_VERIFY_TOKEN)"
```

**Expected Output:**
The `-v` (verbose) flag will show the HTTP status code. You are looking for a `200 OK` response.

  - ✅ **Correct (`< HTTP/2 200`):** Your endpoint is live and working.
  - ❌ **Incorrect (`< HTTP/2 403`):** Your endpoint is rejecting the request due to a token mismatch. Check that the `STRAVA_VERIFY_TOKEN` value in AWS Secrets Manager is correct and that your application is configured to load it.
  - ❌ **Incorrect (`< HTTP/2 404`):** The `/strava_webhook` URL was not found on your server. Check that your latest `app.py` with the correct route is deployed.

### Step 3: Check Your App Runner Logs for Live Events

If the first two steps pass, the final check is to trigger a real event and see if it appears in your application logs.

1.  Go to your **App Runner service** in the AWS Console and open the **"Logs"** tab.
2.  On the **Strava website** or in the app, perform an action that triggers an update. The simplest is to **edit the title or description of your latest activity** and save it.
3.  Immediately check the App Runner logs. Within a few seconds, a new log entry should appear:

<!-- end list -->

```
--- Webhook event received: {'object_type': 'activity', 'object_id': 123456789, ...} ---
```

  - If you see this log entry, your webhook system is fully operational.
  - If you do not see this entry, it could indicate a temporary delivery issue from Strava, but this is rare if steps 1 and 2 were successful.

<!-- end list -->
