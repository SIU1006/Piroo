import os

from locust import HttpUser, between, task


class VideoStreamingUser(HttpUser):
    wait_time = between(1, 3)
    host = "http://localhost:8080"

    @task
    def upload_video(self):
        test_file_path = "test_videos/test_eng_2.mp4"
        with open(test_file_path, "rb") as f:
            self.client.post(
                "/api/v1/upload",
                files={"file": (os.path.basename(test_file_path), f, "audio/mpeg")},
            )
