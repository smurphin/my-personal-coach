# === STAGE 1: CSS Asset Builder ===
# This stage's only job is to build the minified CSS file.
FROM node:18-alpine AS css-builder

WORKDIR /app

# Copy all the necessary files for the CSS build process
COPY package*.json tailwind.config.js postcss.config.js ./
COPY static/src/input.css ./static/src/

# Install npm dependencies
RUN npm install

# Run the production build command to generate the final, minified CSS file
RUN npx tailwindcss -i ./static/src/input.css -o ./static/css/styles.css --minify


# === STAGE 2: Python Dependency Builder ===
# This is from your working file. It installs Python packages efficiently.
FROM python:3.11-slim AS python-dependency-builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# === STAGE 3: The Final Production Image ===
# Start from a clean Python base to keep the image small and secure.
FROM python:3.11-slim

WORKDIR /app

# Create a non-root user for security
RUN useradd --create-home appuser

# Copy the installed Python dependencies from the dependency-builder stage
COPY --from=python-dependency-builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-dependency-builder /usr/local/bin /usr/local/bin

# Copy the application source code
# We copy everything EXCEPT the files handled in other steps or ignored by .dockerignore
COPY . .

# Copy ONLY the final, minified CSS from the css-builder stage into the correct location
COPY --from=css-builder /app/static/css/styles.css ./static/css/styles.css

# Set ownership of the app directory to our non-root user
RUN chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Set the environment variable for production
ENV FLASK_ENV=production

# Expose the port and run the app using your proven gunicorn command
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "300", "app:app"]