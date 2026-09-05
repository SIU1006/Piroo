import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# (label, upload_avg_ms, e2e_avg_s, e2e_p95_s, e2e_completed, upload_rps, e2e_rps)
data = [
    ("5 users\n(solo)",    2063, 26.7, 49,  5, 0.18, 0.09),
    ("25 users\n(solo)",   2046, 60.4, 104, 9, 0.30, 0.08),
    ("25 users\n(threads)",2124, 42.0, 85,  9, 0.38, 0.10),
    ("50 users\n(threads)",2063, 70.8, 77,  3, 0.63, 0.04),
]
labels = [d[0] for d in data]

# 1. Upload latency (ms)
plt.figure(figsize=(5,3))
plt.bar(labels, [d[1] for d in data], color="#4c9f70")
plt.ylabel("Upload latency (ms)")
plt.title("POST /upload latency vs concurrency")
plt.ylim(0, 2500)
plt.tight_layout(); plt.savefig("docs/loadtest/upload_latency.png", dpi=110); plt.close()

# 2. End-to-end latency (s)
plt.figure(figsize=(5,3))
import numpy as np
x = np.arange(len(labels))
w = 0.35
plt.bar(x-w/2, [d[2] for d in data], w, label="avg", color="#3b7cbf")
plt.bar(x+w/2, [d[3] for d in data], w, label="p95", color="#e07b39")
plt.xticks(x, labels)
plt.ylabel("End-to-end latency (s)")
plt.title("Upload->result latency vs concurrency")
plt.legend()
plt.tight_layout(); plt.savefig("docs/loadtest/e2e_latency.png", dpi=110); plt.close()

# 3. Throughput
plt.figure(figsize=(5,3))
x = np.arange(len(labels))
plt.bar(x-w/2, [d[5] for d in data], w, color="#9b59b6", label="uploads/s")
plt.bar(x+w/2, [d[6] for d in data], w, color="#c0392b", label="results/s")
plt.xticks(x, labels)
plt.ylabel("Throughput (req/s)")
plt.title("Throughput vs concurrency")
plt.legend()
plt.tight_layout(); plt.savefig("docs/loadtest/throughput.png", dpi=110); plt.close()
print("charts written")
