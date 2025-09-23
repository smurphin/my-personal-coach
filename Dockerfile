#--- Stage 1: The Builder ---
# This stage's only job is to install the Python dependencies.
FROM python:3.11-slim as builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

#--- Stage 2: The Final Image ---
# This is the small, clean image that will actually run in production.
FROM python:3.11-slim

WORKDIR /app

# Create a non-root user
RUN useradd --create-home appuser
USER appuser

# Copy the installed dependencies AND the executables from the "builder" stage.
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy the application code
COPY . .
ENV FLASK_ENV=production
# Expose the port and run the app
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "300", "app:app"]