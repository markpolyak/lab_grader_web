import assert from "node:assert/strict";
import test from "node:test";

// Тест лежит здесь, а не в src/api/: sh раскрывает глоб npm test
// `src/**/*.test.js` как src/*/*.test.js, и тогда запустился бы только он.
import { createQueueApi, queueErrorCode } from "../../api/queue.js";
import { createPoller } from "./state.js";


function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => {
      if (body === undefined) throw new SyntaxError("Unexpected end of JSON input");
      return body;
    },
  };
}

function recordingFetch(response) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return typeof response === "function" ? response(url, options) : response;
  };
  return { calls, fetchImpl };
}


test("GET состояния очереди: адрес, cookie и таймаут", async () => {
  const { calls, fetchImpl } = recordingFetch(jsonResponse(200, { sessions: [] }));
  const api = createQueueApi({ baseUrl: "https://labgrader.example.ru/api/v1/", fetchImpl });

  const data = await api.getState("os-2026");

  assert.deepEqual(data, { sessions: [] });
  assert.equal(calls[0].url, "https://labgrader.example.ru/api/v1/courses/os-2026/queue");
  assert.equal(calls[0].options.method, "GET");
  // Логин студента живёт в HttpOnly cookie - без include она не уедет на :8000
  assert.equal(calls[0].options.credentials, "include");
  assert.ok(calls[0].options.signal);
  assert.equal(calls[0].options.body, undefined);
});

test("запись в очередь отправляет только сессию и работу, без логина", async () => {
  const { calls, fetchImpl } = recordingFetch(jsonResponse(200, { sessions: [] }));
  const api = createQueueApi({ baseUrl: "http://localhost:8000", fetchImpl });

  await api.signUp("os-2026", { sessionId: 4, labId: "3" });

  assert.equal(calls[0].url, "http://localhost:8000/courses/os-2026/queue/entries");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), { session_id: 4, lab_id: "3" });
});

test("leave, skip и logout - POST без тела", async () => {
  const { calls, fetchImpl } = recordingFetch(jsonResponse(200, {}));
  const api = createQueueApi({ baseUrl: "", fetchImpl });

  await api.leave("c");
  await api.skip("c");
  await api.logout("c");

  assert.deepEqual(
    calls.map((c) => [c.url, c.options.method, c.options.body]),
    [
      ["/courses/c/queue/leave", "POST", undefined],
      ["/courses/c/queue/skip", "POST", undefined],
      ["/courses/c/queue/logout", "POST", undefined],
    ]
  );
});

test("адрес входа через GitHub несёт выбранную работу", () => {
  const api = createQueueApi({ baseUrl: "http://localhost:8000", fetchImpl: async () => null });
  assert.equal(api.loginUrl("os-2026"), "http://localhost:8000/courses/os-2026/queue/login");
  assert.equal(
    api.loginUrl("os-2026", "ЛР3"),
    "http://localhost:8000/courses/os-2026/queue/login?lab=%D0%9B%D0%A03"
  );
});

test("админские маршруты лежат под /admin/ и передают параметры как в контракте", async () => {
  const { calls, fetchImpl } = recordingFetch(jsonResponse(200, {}));
  const api = createQueueApi({ baseUrl: "/api/v1", fetchImpl });

  await api.getDay("os");
  await api.callNext("os", 7, 42);
  await api.callNext("os", 7, null);
  await api.setOutcome("os", 42, "no_show");
  await api.returnToQueue("os", 42);
  await api.openManual("os", { groups: [], closesAt: "" });
  await api.openManual("os", { groups: ["4431"], closesAt: "18:30" });
  await api.closeSignup("os", 7);
  await api.reopenSignup("os", 7);
  await api.finishSession("os", 7);

  assert.deepEqual(
    calls.map((c) => [c.options.method, c.url, c.options.body && JSON.parse(c.options.body)]),
    [
      ["GET", "/api/v1/admin/courses/os/queue", undefined],
      ["POST", "/api/v1/admin/courses/os/queue/sessions/7/call-next", { expected_entry_id: 42 }],
      ["POST", "/api/v1/admin/courses/os/queue/sessions/7/call-next", { expected_entry_id: null }],
      ["POST", "/api/v1/admin/courses/os/queue/entries/42/outcome", { outcome: "no_show" }],
      ["POST", "/api/v1/admin/courses/os/queue/entries/42/return", undefined],
      ["POST", "/api/v1/admin/courses/os/queue/sessions", { groups: [], closes_at: null }],
      ["POST", "/api/v1/admin/courses/os/queue/sessions", { groups: ["4431"], closes_at: "18:30" }],
      ["POST", "/api/v1/admin/courses/os/queue/sessions/7/close", undefined],
      ["POST", "/api/v1/admin/courses/os/queue/sessions/7/reopen", undefined],
      ["POST", "/api/v1/admin/courses/os/queue/sessions/7/finish", undefined],
    ]
  );
  assert.ok(calls.every((c) => c.options.credentials === "include"));
});

test("история: пустые фильтры в запрос не попадают", async () => {
  const { calls, fetchImpl } = recordingFetch(jsonResponse(200, { entries: [] }));
  const api = createQueueApi({ baseUrl: "/api/v1", fetchImpl });

  await api.getHistory("os", { from: "2026-10-01", to: "", group: "4431", q: "Иван", lab: null });
  await api.getHistory("os");

  assert.equal(
    calls[0].url,
    "/api/v1/admin/courses/os/queue/history?from=2026-10-01&group=4431&q=%D0%98%D0%B2%D0%B0%D0%BD"
  );
  assert.equal(calls[1].url, "/api/v1/admin/courses/os/queue/history");
});

test("код ошибки берётся из detail backend", async () => {
  const { fetchImpl } = recordingFetch(jsonResponse(409, { detail: "SIGNUP_CLOSED" }));
  const api = createQueueApi({ baseUrl: "", fetchImpl });

  await assert.rejects(api.signUp("c", { sessionId: 1, labId: "1" }), (error) => {
    assert.equal(error.code, "SIGNUP_CLOSED");
    assert.equal(error.status, 409);
    assert.deepEqual(error.payload, { detail: "SIGNUP_CLOSED" });
    return true;
  });
});

test("без кода в detail код выводится из статуса", () => {
  assert.equal(queueErrorCode(401, "Нет сессии"), "SESSION_REQUIRED");
  assert.equal(queueErrorCode(404, "Course not found"), "not_found");
  assert.equal(queueErrorCode(429, undefined), "rate_limit");
  assert.equal(queueErrorCode(422, [{ loc: ["body"], msg: "x" }]), "unknown");
  assert.equal(queueErrorCode(500, "Internal error"), "unknown");
  assert.equal(queueErrorCode(503, "STORAGE_UNAVAILABLE"), "STORAGE_UNAVAILABLE");
});

test("пустое тело ошибки не ломает разбор", async () => {
  const { fetchImpl } = recordingFetch(jsonResponse(429, undefined));
  const api = createQueueApi({ baseUrl: "", fetchImpl });
  await assert.rejects(api.getState("c"), (error) => error.code === "rate_limit");
});

test("сбой сети и таймаут дают свои коды", async () => {
  const offline = createQueueApi({
    baseUrl: "",
    fetchImpl: async () => {
      throw new TypeError("Failed to fetch");
    },
  });
  await assert.rejects(offline.getState("c"), (error) => error.code === "network_error");

  // fetch, который отвечает только на отмену - как зависший backend
  const hanging = createQueueApi({
    baseUrl: "",
    timeoutMs: 20,
    fetchImpl: (url, options) =>
      new Promise((resolve, reject) => {
        options.signal.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        });
      }),
  });
  await assert.rejects(hanging.getState("c"), (error) => error.code === "request_timeout");
});

// Ответ 200: заголовки пришли, а тело оборвалось на середине JSON и дальше
// не идёт, пока запрос не отменят. Чем поток ошибается при отмене, зависит
// от реализации fetch, поэтому ошибку можно подменить.
function stalledBodyFetch(abortError = () => new DOMException("Aborted", "AbortError")) {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{"sessions":'));
          options.signal.addEventListener("abort", () => controller.error(abortError()));
        },
      }),
      { status: 200 }
    );
  };
  return { calls, fetchImpl };
}

// Без таймаута на тело запрос висит вечно: тест должен упасть, а не зависнуть.
function settlesWithin(promise, ms) {
  let guard;
  const timeout = new Promise((resolve, reject) => {
    guard = setTimeout(() => reject(new Error(`запрос не завершился за ${ms} мс`)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(guard));
}

test("таймаут действует и на чтение тела: застрявший JSON - request_timeout", async () => {
  const { calls, fetchImpl } = stalledBodyFetch();
  const api = createQueueApi({ baseUrl: "", timeoutMs: 20, fetchImpl });

  await assert.rejects(settlesWithin(api.getState("c"), 500), (error) => {
    assert.equal(error.code, "request_timeout");
    assert.equal(error.status, 0);
    assert.deepEqual(error.payload, {});
    return true;
  });
  assert.equal(calls[0].options.signal.aborted, true);
});

test("застрявшее тело не останавливает опрос: после таймаута он повторяется", async () => {
  const { calls, fetchImpl } = stalledBodyFetch();
  const api = createQueueApi({ baseUrl: "", timeoutMs: 20, fetchImpl });
  let completed = 0;
  const poller = createPoller(async () => {
    try {
      await api.getState("c");
    } catch {
      // ошибку опроса показывает компонент, здесь важен только повтор
    }
    completed += 1;
    return 5;
  });

  poller.start();
  await new Promise((resolve) => setTimeout(resolve, 100));
  // run() зовут возврат на вкладку и обновление после действия
  poller.run();
  await new Promise((resolve) => setTimeout(resolve, 30));
  poller.stop();

  assert.ok(completed >= 1, "опрос должен завершиться по таймауту");
  assert.ok(calls.length > 1, "после таймаута опрос должен повториться");
});

test("оборванное тело ответа на действие преподавателя - request_timeout", async () => {
  // решает отменённый сигнал, а не имя ошибки потока
  const { calls, fetchImpl } = stalledBodyFetch(() => new TypeError("terminated"));
  const api = createQueueApi({ baseUrl: "/api/v1", timeoutMs: 20, fetchImpl });

  await assert.rejects(settlesWithin(api.setOutcome("os", 42, "defended"), 500), (error) => {
    assert.equal(error.code, "request_timeout");
    assert.equal(error.status, 0);
    return true;
  });
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.signal.aborted, true);
});
