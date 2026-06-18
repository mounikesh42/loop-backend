(function () {
  var apiOrigin = (window.LOOP_API_ORIGIN || "").replace(/\/$/, "");
  var form = document.getElementById("uploadForm");
  var targetSelect = document.getElementById("target");
  var submitBtn = document.getElementById("submitBtn");
  var statusText = document.getElementById("status");
  var log = document.getElementById("log");
  var pollTimer = null;

  function api(path) {
    return apiOrigin + path;
  }

  function write(value) {
    log.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }

  function append(line) {
    log.textContent += "\n" + line;
  }

  function setBusy(isBusy, text) {
    submitBtn.disabled = isBusy;
    statusText.textContent = text || "";
  }

  function visibleGroups() {
    var target = targetSelect.value;
    return Array.prototype.filter.call(document.querySelectorAll(".group"), function (group) {
      return group.getAttribute("data-targets").split(/\s+/).indexOf(target) !== -1;
    });
  }

  function refreshGroups() {
    var target = targetSelect.value;
    Array.prototype.forEach.call(document.querySelectorAll(".group"), function (group) {
      var active = group.getAttribute("data-targets").split(/\s+/).indexOf(target) !== -1;
      group.hidden = !active;
      Array.prototype.forEach.call(group.querySelectorAll("input[type='file']"), function (input) {
        input.required = active;
      });
    });
  }

  function selectedFileCount() {
    return visibleGroups().reduce(function (count, group) {
      var input = group.querySelector("input[type='file']");
      return count + (input ? input.files.length : 0);
    }, 0);
  }

  function missingRequiredGroups() {
    return visibleGroups().filter(function (group) {
      var input = group.querySelector("input[type='file']");
      return !input || !input.files.length;
    }).map(function (group) {
      var label = group.querySelector("label");
      return label ? label.textContent : group.getAttribute("data-targets");
    });
  }

  function errorMessage(text) {
    if (!text) return "Request failed.";
    try {
      var parser = new DOMParser();
      var doc = parser.parseFromString(text, "text/html");
      var body = doc.querySelector("p");
      return body && body.textContent ? body.textContent : text;
    } catch (err) {
      return text;
    }
  }

  function requestJson(path, options) {
    return fetch(api(path), options).then(function (res) {
      return res.text().then(function (text) {
        if (!res.ok) throw new Error(errorMessage(text) || ("HTTP " + res.status));
        return text ? JSON.parse(text) : {};
      });
    });
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function pollJob(jobId) {
    requestJson("/api/jobs/" + encodeURIComponent(jobId))
      .then(function (job) {
        write(job);
        statusText.textContent = "Job status: " + job.status;
        if (job.status === "completed") {
          stopPolling();
          setBusy(false, "Completed.");
          append("");
          append("Results metadata: " + api("/api/jobs/" + jobId + "/results"));
          append("Dashboard API: " + api("/api/check-point/indicators"));
          return;
        }
        if (job.status === "failed" || job.status === "expired") {
          stopPolling();
          setBusy(false, "Job " + job.status + ".");
        }
      })
      .catch(function (err) {
        stopPolling();
        setBusy(false, "Status request failed.");
        append("");
        append(err.message || String(err));
      });
  }

  targetSelect.addEventListener("change", function () {
    refreshGroups();
    write("Waiting for " + targetSelect.options[targetSelect.selectedIndex].text + " files...");
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    stopPolling();

    var missing = missingRequiredGroups();
    if (missing.length) {
      write("Choose files for: " + missing.join(", "));
      return;
    }

    var target = targetSelect.value;
    var body = new FormData();
    visibleGroups().forEach(function (group) {
      var input = group.querySelector("input[type='file']");
      Array.prototype.forEach.call(input.files, function (file) {
        body.append(input.name, file, file.webkitRelativePath || file.name);
      });
    });

    setBusy(true, "Uploading " + selectedFileCount() + " file(s)...");
    write("Uploading " + selectedFileCount() + " file(s) for target: " + target);

    requestJson("/api/jobs", {
      method: "POST",
      body: body
    })
      .then(function (created) {
        write(created);
        setBusy(true, "Starting validation...");
        return requestJson("/api/jobs/" + encodeURIComponent(created.job_id) + "/validate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target: target })
        }).then(function (queued) {
          write(queued);
          pollJob(created.job_id);
          pollTimer = setInterval(function () {
            pollJob(created.job_id);
          }, 2500);
        });
      })
      .catch(function (err) {
        stopPolling();
        setBusy(false, "Upload or validation failed.");
        write(err.message || String(err));
      });
  });

  refreshGroups();
})();
