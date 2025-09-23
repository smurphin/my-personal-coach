# === STAGE 1: CSS Asset Builder ===
FROM node:18-alpine AS css-builder

WORKDIR /app

# Copy the Node.js build config files and source CSS
COPY package.json package-lock.json ./
COPY tailwind.config.js postcss.config.js ./
COPY static/src/input.css ./static/src/

# **IMPORTANT: Copy the HTML templates for Tailwind to scan**
COPY templates ./templates

# Install npm dependencies
RUN npm install

# Run the production build command to generate the final, minified CSS file
RUN npm run build:prod

# --- STAGE 2: Python Dependency Builder (Unchanged) ---
FROM python:3.11-slim AS python-dependency-builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- STAGE 3: The Final Production Image (Minor change) ---
FROM python:3.11-slim

WORKDIR /app

RUN useradd --create-home appuser

COPY --from=python-dependency-builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-dependency-builder /usr/local/bin /usr/local/bin

# This copies everything from the root, including the templates
# so they are available for your Python app.
COPY . .

# This step is still correct, as it grabs the newly generated CSS
# and replaces the empty one from the `COPY . .` command.
COPY --from=css-builder /app/static/css/styles.css ./static/css/styles.css

RUN chown -R appuser:appuser /app

USER appuser

ENV FLASK_ENV=production

EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "300", "app:app"]