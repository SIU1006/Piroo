import mlflow

mlflow.set_tracking_uri("http://localhost:3002")

with mlflow.start_run(run_name="whisper-base"):
    mlflow.log_param("model_size", "base")
    mlflow.log_param("device", "cpu")
    mlflow.log_param("compute_type", "int8")
    mlflow.log_metric("approx_rtf", 1.0)

with mlflow.start_run(run_name="whisper-small"):
    mlflow.log_param("model_size", "small")
    mlflow.log_param("device", "cpu")
    mlflow.log_param("compute_type", "int8")
    mlflow.log_metric("approx_rtf", 2.0)

with mlflow.start_run(run_name="whisper-medium"):
    mlflow.log_param("model_size", "medium")
    mlflow.log_param("device", "cpu")
    mlflow.log_param("compute_type", "int8")
    mlflow.log_metric("approx_rtf", 5.0)
