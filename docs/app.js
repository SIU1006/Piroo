const API_BASE = "http://localhost:8080";
const WS_BASE = "ws://localhost:8080";

function setStage(text, state){
  const dot = document.getElementById("stage_dot");
  const el = document.getElementById("stage_text");
  dot.className = "dot " + state;
  el.textContent = text;
  el.classList.remove("empty");
}

function setSummary(text, state, taskId){
  const dot = document.getElementById("summary_dot");
  const el = document.getElementById("summary_text");
  dot.className = "dot " + state;
  el.textContent = text;
  el.classList.remove("empty");
  if (taskId){
    document.getElementById("meta").style.display = "block";
    document.getElementById("meta_task_id").textContent = taskId;
  }
}

function connect() {
  const task_id = document.getElementById("task_id").value;
  if (!task_id) {
    setStage("Enter a task_id first", "error");
    return;
  }

  setStage("Connecting…", "active");
  const summaryEl = document.getElementById("summary_text");
  const summaryDot = document.getElementById("summary_dot");
  summaryEl.textContent = "Waiting for task result";
  summaryEl.classList.add("empty");
  summaryDot.className = "dot pending";
  document.getElementById("meta").style.display = "none";
  document.getElementById("meta_task_id").textContent = "";

  const ws = new WebSocket(`${WS_BASE}/api/v1/ws/${task_id}`);

  ws.onopen = () => setStage("Connected. Waiting for result…", "active");

  ws.onmessage = (event) => {
    let data;
    try {
      data = JSON.parse(event.data);
    } catch (e) {
      setSummary(event.data, "done");
      return;
    }

    if (data.status === "completed") {
      setStage("Completed", "done");
      setSummary(data.summary, "done", data.task_id);
    } else if (data.status === "error") {
      setStage("error", "error");
      setSummary(data.error || "An error occurred", "error", data.task_id);
    }
  };

  ws.onclose = () => {
    const dot = document.getElementById("stage_dot");
    if (!dot.classList.contains("done") && !dot.classList.contains("error")) {
      setStage("Connection closed", "pending");
    }
  };

  ws.onerror = () => setStage("Connection error", "error");
}