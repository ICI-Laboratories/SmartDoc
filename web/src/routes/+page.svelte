<script lang="ts">
  import { onMount } from "svelte";
  import PdfViewer from "$lib/PdfViewer.svelte";
  import { track, setMeasurement } from "$lib/analytics";
  let measurement = $state(true),
    measurementAvailable = $state(true),
    savingPreference = $state(false);
  let retentionDays = $state(90);
  let feedback = $state<"positive" | "negative" | null>(null);
  import {
    request,
    uploadFile,
    csrf,
    ApiError,
    states,
    bytes,
    type Document,
    type Catalog,
    type Hit,
    type Source,
  } from "$lib/api";
  let accessMode = $state("choose"),
    loginAvailable = $state(false),
    expiresIn = $state(86400);
  let leaving = $state(false);
  let sessionReady = $state(false),
    checking = $state(true),
    error = $state(""),
    notice = $state("");
  let catalog = $state<Catalog>({
    items: [],
    next: null,
    stats: { total: 0, pending: 0, bytes: 0 },
  });
  let selected = $state<Document | null>(null),
    page = $state(1),
    panel = $state<"info" | "chat">("info");
  let picked = $state<string[]>([]),
    extraDocs = $state<string[]>([]),
    comparing = $state(false);
  let comparison = $state<{
    documents: { id: string; name: string }[];
    pairs: { id: string; other_id: string; similarity: number }[];
  } | null>(null);
  let query = $state(""),
    searchMode = $state<"name" | "text" | "hybrid">("name"),
    searching = $state(false),
    results = $state<Hit[] | null>(null),
    retrievalMode = $state("");
  let filter = $state("all"),
    dark = $state(false),
    dragging = $state(false);
  let uploads = $state<{ name: string; progress: number; state: string }[]>([]);
  let question = $state(""),
    answer = $state(""),
    sources = $state<Source[]>([]),
    asking = $state(false),
    chatError = $state("");
  let fileInput = $state<HTMLInputElement>(undefined!);
  let searchController: AbortController | undefined;
  let chatController: AbortController | undefined;
  let searchTimer: ReturnType<typeof setTimeout>;
  let lastRequest = 0;
  let visible = $derived(
    catalog.items.filter(
      (d) =>
        filter === "all" ||
        (filter === "pending"
          ? ["queued", "processing"].includes(d.status)
          : d.status === "failed"),
    ),
  );
  function handleError(e: unknown) {
    if (e instanceof ApiError && e.status === 401) sessionReady = false;
    error = (e as Error).message;
  }
  async function load(more = false) {
    const id = ++lastRequest;
    const params = new URLSearchParams({
      q: searchMode === "name" ? query : "",
      status: filter,
    });
    if (more && catalog.next) {
      params.set("before", catalog.next.before);
      params.set("before_id", catalog.next.before_id);
    }
    try {
      const data = await request<Catalog>(`/api/documents?${params}`);
      if (id !== lastRequest) return;
      catalog = more
        ? { ...data, items: [...catalog.items, ...data.items] }
        : data;
      if (selected) {
        const selectedId = selected.id;
        const detail =
          data.items.find((d) => d.id === selectedId) ??
          (await request<Document>(`/api/documents/${selectedId}`));
        if (selected?.id === selectedId) selected = detail;
      }
    } catch (e) {
      handleError(e);
    }
  }
  async function doSearch() {
    searchController?.abort();
    error = "";
    if (searchMode === "name" || !query.trim()) {
      results = null;
      await load();
      return;
    }
    const controller = new AbortController();
    searchController = controller;
    searching = true;
    try {
      const data = await request<{ items: Hit[]; mode: string }>(
        `/api/search?${new URLSearchParams({ q: query, semantic: String(searchMode === "hybrid") })}`,
        { signal: controller.signal },
      );
      if (!controller.signal.aborted) {
        results = data.items;
        retrievalMode = data.mode;
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") handleError(e);
    } finally {
      if (searchController === controller) searching = false;
    }
  }
  function scheduleSearch() {
    clearTimeout(searchTimer);
    searchController?.abort();
    searchTimer = setTimeout(doSearch, 300);
  }
  async function openDocument(doc: Document, at = 1) {
    chatController?.abort();
    selected = doc;
    page = at;
    panel = "info";
    answer = "";
    feedback = null;
    sources = [];
    chatError = "";
    question = "";
    extraDocs = [];
    asking = false;
  }
  async function openHit(hit: Hit) {
    try {
      await openDocument(
        await request<Document>(`/api/documents/${hit.id}`),
        hit.page,
      );
    } catch (e) {
      handleError(e);
    }
  }
  async function addFiles(files: FileList | File[]) {
    error = "";
    notice = "";
    // Two concurrent transfers at most, independent of OCR/LLM work.
    const queue = Array.from(files);
    const consume = async () => {
      while (queue.length) {
        const file = queue.shift()!;
        const item = { name: file.name, progress: 0, state: "Subiendo" };
        const index = uploads.length;
        uploads = [...uploads, item];
        if (
          !file.name.toLowerCase().endsWith(".pdf") ||
          file.size > 100 * 1024 * 1024
        ) {
          uploads[index].state = "Selecciona un PDF de hasta 100 MB";
          continue;
        }
        try {
          const data = await uploadFile(file, (n) => {
            uploads[index].progress = n;
          });
          uploads[index].progress = 100;
          uploads[index].state = data.duplicate
            ? "Ya estaba en tu biblioteca"
            : "Guardado";
          notice =
            "Tus archivos guardados ya se pueden abrir. El texto se procesará en segundo plano.";
          await load();
        } catch (e) {
          uploads[index].state = (e as Error).message;
        }
      }
    };
    await Promise.all([consume(), consume()]);
    fileInput.value = "";
  }
  async function retry(doc: Document) {
    try {
      await request(`/api/documents/${doc.id}/retry`, { method: "POST" });
      await load();
    } catch (e) {
      handleError(e);
    }
  }
  async function openSource(source: Source) {
    track("citation_open");
    if (selected?.id === source.id) page = source.page;
    else
      try {
        await openDocument(
          await request<Document>(`/api/documents/${source.id}`),
          source.page,
        );
      } catch (e) {
        handleError(e);
      }
  }
  async function generateSummary() {
    if (!selected) return;
    try {
      await request(`/api/documents/${selected.id}/summary`, {
        method: "POST",
      });
      await load();
    } catch (e) {
      handleError(e);
    }
  }
  async function compare() {
    comparing = true;
    error = "";
    try {
      comparison = await request("/api/similarity", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_ids: picked }),
      });
    } catch (e) {
      handleError(e);
    } finally {
      comparing = false;
    }
  }
  function similarity(a: string, b: string) {
    return (
      comparison?.pairs.find((p) => p.id === a && p.other_id === b)
        ?.similarity ?? 0
    );
  }
  async function startSession(create = false) {
    if (create) {
      searchController?.abort();
      chatController?.abort();
      ++lastRequest;
      selected = null;
      answer = "";
      question = "";
      sources = [];
      comparison = null;
      results = null;
      query = "";
      picked = [];
      extraDocs = [];
      uploads = [];
      catalog = {items: [], next: null, stats: {total: 0, pending: 0, bytes: 0}};
    }
    checking = true;
    error = "";
    try {
      const session = await request<{
        mode: string;
        login_available: boolean;
        expires_in?: number;
        ephemeral_hours?: number;
      }>(
        create ? "/session/anonymous" : "/session/me",
        create ? { method: "POST" } : {},
      );
      accessMode = session.mode;
      loginAvailable = session.login_available;
      expiresIn = session.expires_in ?? (session.ephemeral_hours ?? 24) * 3600;
      sessionReady = session.mode !== "choose";
      if (!sessionReady) {
        setMeasurement(false);
        return;
      }
      await load();
      try {
        const prefs = await request<{
          enabled: boolean;
          available: boolean;
          retention_days: number;
        }>("/api/analytics/preferences");
        measurement = prefs.enabled;
        measurementAvailable = prefs.available;
        retentionDays = prefs.retention_days;
        setMeasurement(measurement);
        track("library_view");
      } catch {
        setMeasurement(false);
        measurement = false;
      }
    } catch (e) {
      handleError(e);
    } finally {
      checking = false;
    }
  }
  async function endSession() {
    leaving = true;
    try {
      await request("/session/end", { method: "POST" });
      setMeasurement(false);
      window.location.reload();
    } catch (e) {
      handleError(e);
    } finally {
      leaving = false;
    }
  }
  async function changeMeasurement() {
    savingPreference = true;
    try {
      const pref = await request<{ enabled: boolean }>(
        "/api/analytics/preferences",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: !measurement }),
        },
      );
      measurement = pref.enabled;
      setMeasurement(measurement);
    } catch (e) {
      handleError(e);
    } finally {
      savingPreference = false;
    }
  }
  function rateAnswer(value: "positive" | "negative") {
    if (feedback) return;
    feedback = value;
    track("answer_feedback", value);
  }
  async function ask() {
    if (!selected || !question.trim() || asking) return;
    const controller = new AbortController();
    chatController = controller;
    asking = true;
    answer = "";
    feedback = null;
    sources = [];
    chatError = "";
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
        body: JSON.stringify({
          question,
          document_ids: [selected.id, ...extraDocs],
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || "No se pudo iniciar la consulta.");
      }
      const reader = response.body!.getReader(),
        decoder = new TextDecoder();
      let pending = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        let boundary;
        while ((boundary = pending.indexOf("\n\n")) >= 0) {
          const event = pending.slice(0, boundary);
          pending = pending.slice(boundary + 2);
          if (event.startsWith("data: ")) {
            const data = JSON.parse(event.slice(6));
            if (data.token) answer += data.token;
            if (data.sources) sources = data.sources;
            if (data.error) chatError = data.error;
          }
        }
      }
    } catch (e) {
      if ((e as Error).name !== "AbortError") chatError = (e as Error).message;
    } finally {
      if (chatController === controller) asking = false;
    }
  }
  function toggleTheme() {
    dark = !dark;
    document.documentElement.dataset.saraTheme = dark ? "dark" : "light";
    localStorage.setItem("sara-theme", dark ? "dark" : "light");
    track("theme_changed", dark ? "dark" : "light");
  }
  onMount(() => {
    dark =
      localStorage.getItem("sara-theme") === "dark" ||
      (!localStorage.getItem("sara-theme") &&
        matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.saraTheme = dark ? "dark" : "light";
    void startSession();
    const poll = setInterval(() => {
      if (
        sessionReady &&
        catalog.stats.pending > 0 &&
        !query &&
        !document.hidden
      )
        void load();
    }, 4000);
    return () => {
      clearInterval(poll);
      clearTimeout(searchTimer);
      searchController?.abort();
      chatController?.abort();
    };
  });
</script>

<svelte:head
  ><title>SARA DocReader · Biblioteca documental</title><meta
    name="description"
    content="Tu biblioteca de documentos del laboratorio. Organiza, encuentra y lee en un solo lugar."
  /></svelte:head
>
<a class="skip-link" href="#main">Saltar al contenido</a>
<div class="app-shell">
  <aside class="sidebar">
    <a href="/" class="brand" aria-label="SARA DocReader, inicio"
      ><img
        src={dark ? "/brand/sara-dark.svg" : "/brand/sara-light.svg"}
        alt="SARA"
      /><span>DocReader</span></a
    >
    <div class="workspace-label">ESPACIO DE TRABAJO</div>
    <div class="workspace">
      <span class="workspace-icon">L</span>
      <div>
        <strong>Laboratorio</strong><small>Biblioteca documental</small>
      </div>
    </div>
    <nav aria-label="Biblioteca">
      <button
        disabled={!sessionReady}
        class:active={sessionReady && filter === "all"}
        onclick={() => {
          filter = "all";
          selected = null;
          void load();
        }}><span>▤</span> Mi biblioteca <b>{catalog.stats.total}</b></button
      >
      <button
        disabled={!sessionReady}
        class:active={sessionReady && filter === "pending"}
        onclick={() => {
          filter = "pending";
          selected = null;
          void load();
        }}
        ><span>◷</span> En procesamiento <b>{catalog.stats.pending}</b></button
      >
      <button
        disabled={!sessionReady}
        class:active={sessionReady && filter === "failed"}
        onclick={() => {
          filter = "failed";
          selected = null;
          void load();
        }}><span>!</span> Por revisar</button
      >
    </nav>
    <div class="sidebar-note">
      <span class="small-rule"></span><strong>El conocimiento, a mano.</strong>
      <p>Tus documentos y sus fuentes, en un mismo lugar.</p>
    </div>
    <footer>
      <button class="quiet" onclick={toggleTheme}
        >{dark ? "☀ Tema claro" : "☾ Tema oscuro"}</button
      ><small>SARA · ICI Laboratories</small>
    </footer>
  </aside>
  <div class="main-shell">
    <header class="topbar">
      <span>Laboratorio <span class="slash">/</span> DocReader</span><span
        class="private-badge"
        >◉ {accessMode === "account"
          ? "Con cuenta"
          : sessionReady
            ? "Sin cuenta"
            : "Elige tu acceso"}</span
      >
      {#if sessionReady}<button
          class="quiet"
          disabled={leaving}
          onclick={endSession}
          >{accessMode === "account"
            ? "Cerrar sesión"
            : accessMode === "ephemeral"
              ? "Terminar y borrar espacio"
              : "Salir del espacio"}</button
        >{/if}
    </header>
    <main id="main">
      {#if checking}<div class="empty">
          <span class="eyebrow">SARA DOCREADER</span>
          <h1>Abriendo tu biblioteca…</h1>
          <p role="status">Preparando tu espacio, sin registro.</p>
        </div>
      {:else if !sessionReady}
        <section class="welcome">
          <span class="eyebrow">SARA DOCREADER</span>
          <h1>Tu biblioteca, a tu manera.</h1>
          <p>
            Prueba SARA sin cuenta o conserva tus documentos al iniciar sesión.
          </p>
          <div class="access-options">
            <article>
              <h2>Espacio temporal</h2>
              <p>
                Sin registro. Los archivos se borran al terminar el espacio o
                después de {Math.ceil(expiresIn / 3600)} horas. Cerrar la pestaña
                no los elimina de inmediato.
              </p>
              <button class="primary" onclick={() => startSession(true)}
                >Usar temporalmente →</button
              >
            </article>
            <article>
              <h2>Con tu cuenta</h2>
              <p>
                Conserva tu biblioteca y vuelve a ella desde otros dispositivos.
              </p>
              {#if loginAvailable}<a class="primary" href="/auth/login"
                  >Iniciar sesión →</a
                >
              {:else}<button class="secondary" disabled
                  >Iniciar sesión · próximamente</button
                >
                <small
                  >El portal central de SARA se habilitará más adelante.</small
                >{/if}
            </article>
          </div>
          {#if error}<p role="alert">{error}</p>{/if}
        </section>
      {:else}
        <div class="page-heading">
          <div>
            <span class="eyebrow">BIBLIOTECA DOCUMENTAL</span>
            <h1>
              {filter === "pending"
                ? "En procesamiento"
                : filter === "failed"
                  ? "Documentos por revisar"
                  : "Mi biblioteca"}
            </h1>
            <p>Abre, busca y consulta tus documentos. Sin registro.</p>
          </div>
          <button class="primary" onclick={() => fileInput.click()}
            >＋ Subir documentos</button
          >
        </div>
        <input
          class="sr-only"
          tabindex="-1"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          bind:this={fileInput}
          onchange={(e) => {
            if (e.currentTarget.files) void addFiles(e.currentTarget.files);
          }}
          aria-label="Seleccionar documentos PDF"
        />
        <div class="summary-strip">
          <span><strong>{catalog.stats.total}</strong> documentos</span><span
            ><strong>{catalog.stats.pending}</strong> en procesamiento</span
          ><span
            ><strong>{bytes(catalog.stats.bytes)}</strong> en tu biblioteca</span
          ><span class="summary-end">PDF · hasta 100 MB por archivo</span>
        </div>
        <div class="searchbar">
          <span aria-hidden="true">⌕</span><input
            aria-label="Buscar documentos"
            placeholder={searchMode === "name"
              ? "Buscar por nombre del documento…"
              : "¿Qué quieres encontrar en tus documentos?"}
            bind:value={query}
            oninput={scheduleSearch}
          /><select
            aria-label="Tipo de búsqueda"
            bind:value={searchMode}
            onchange={() => {
              track("search_mode_changed", searchMode);
              void doSearch();
            }}
            ><option value="name">Por nombre</option><option value="text"
              >Por contenido</option
            ><option value="hybrid">Por significado</option></select
          >{#if query}<button
              class="icon-button"
              aria-label="Limpiar búsqueda"
              onclick={() => {
                query = "";
                void doSearch();
              }}>×</button
            >{/if}
        </div>
        {#if notice}<p class="notice" role="status">✓ {notice}</p>{/if}
        {#if uploads.length}<section
            class="upload-list"
            aria-label="Estado de las cargas"
          >
            {#each uploads as item}<div>
                <span>{item.name}</span><progress
                  value={item.progress}
                  max="100"
                  aria-label={`Carga de ${item.name}`}
                ></progress><small>{item.state}</small>
              </div>{/each}<button
              class="quiet"
              disabled={uploads.some((u) => u.state === "Subiendo")}
              onclick={() => (uploads = [])}>Ocultar cargas</button
            >
          </section>{/if}
        {#if searching}<p role="status">Buscando en los documentos…</p>{/if}
        {#if results !== null}
          <div class="section-heading">
            <h2>Resultados en el contenido</h2>
            <span>{results.length} fragmentos</span>
          </div>
          {#if retrievalMode === "text_fallback"}<p class="notice">
              La búsqueda semántica no está disponible. Mostramos coincidencias
              de texto.
            </p>{/if}
          {#if results.length === 0}<div class="empty compact">
              <h2>No encontramos coincidencias</h2>
              <p>
                Prueba con otras palabras o espera a que termine el
                procesamiento.
              </p>
            </div>{/if}
          <div class="search-results">
            {#each results as hit}<button
                class="result"
                onclick={() => openHit(hit)}
                ><span class="eyebrow">{hit.name} · PÁGINA {hit.page}</span>
                <p>
                  {hit.content.slice(0, 450)}{hit.content.length > 450
                    ? "…"
                    : ""}
                </p>
                <span class="text-link">Leer en el documento →</span></button
              >{/each}
          </div>
        {:else}
          <div class="section-heading">
            <h2>{query ? "Documentos encontrados" : "Tus documentos"}</h2>
            <div class="list-actions">
              {#if picked.length >= 2}<button
                  class="secondary"
                  disabled={comparing || picked.length > 20}
                  onclick={compare}
                  >{comparing
                    ? "Comparando…"
                    : `Comparar ${picked.length} documentos`}</button
                >{/if}<button class="quiet" onclick={() => load()}
                >↻ Actualizar</button
              >
            </div>
          </div>
          {#if comparison}<section class="comparison">
              <div class="section-heading">
                <h2>Similitud conceptual</h2>
                <button class="quiet" onclick={() => (comparison = null)}
                  >Cerrar comparación</button
                >
              </div>
              <p>
                Similitud del coseno entre vectores promedio; no representa un
                porcentaje de certeza.
              </p>
              <div class="table-wrap">
                <table>
                  <thead
                    ><tr
                      ><th>Documento</th
                      >{#each comparison.documents as doc, i}<th
                          title={doc.name}>{i + 1}</th
                        >{/each}</tr
                    ></thead
                  ><tbody
                    >{#each comparison.documents as doc, i}<tr
                        ><th>{i + 1}. {doc.name}</th
                        >{#each comparison.documents as other}<td
                            style:background={`color-mix(in srgb, var(--sara-action-soft) ${Math.round(Math.max(0, similarity(doc.id, other.id)) * 100)}%, var(--sara-surface))`}
                            >{similarity(doc.id, other.id).toFixed(2)}</td
                          >{/each}</tr
                      >{/each}</tbody
                  >
                </table>
              </div>
            </section>{/if}
          <!-- The labeled region supplements the keyboard-accessible upload button above. -->
          <section
            class:dragging
            class="library"
            aria-label="Lista de documentos y zona de carga"
            ondragover={(e) => {
              e.preventDefault();
              dragging = true;
            }}
            ondragleave={() => (dragging = false)}
            ondrop={(e) => {
              e.preventDefault();
              dragging = false;
              if (e.dataTransfer) void addFiles(e.dataTransfer.files);
            }}
          >
            {#if visible.length}
              <div class="table-wrap">
                <table>
                  <thead
                    ><tr
                      ><th><span class="sr-only">Seleccionar</span></th><th
                        >Documento</th
                      ><th>Estado</th><th>Agregado</th><th>Tamaño</th><th
                        ><span class="sr-only">Acciones</span></th
                      ></tr
                    ></thead
                  ><tbody
                    >{#each visible as doc}<tr
                        ><td
                          ><input
                            class="select-document"
                            type="checkbox"
                            aria-label={`Seleccionar ${doc.name}`}
                            value={doc.id}
                            bind:group={picked}
                          /></td
                        ><td
                          ><button
                            class="document-name"
                            onclick={() => openDocument(doc)}
                            ><span class="pdf-icon">PDF</span><span
                              ><strong>{doc.name}</strong><small
                                >{doc.page_count
                                  ? `${doc.page_count} páginas`
                                  : "Original disponible"}</small
                              ></span
                            ></button
                          ></td
                        ><td
                          ><span class={`status ${doc.status}`}
                            >{states[doc.status]}</span
                          ></td
                        ><td class="muted"
                          >{new Date(doc.created_at).toLocaleDateString(
                            "es-MX",
                            { day: "numeric", month: "short", year: "numeric" },
                          )}</td
                        ><td class="muted">{bytes(doc.size)}</td><td
                          ><button
                            class="icon-button"
                            onclick={() => openDocument(doc)}
                            aria-label={`Abrir ${doc.name}`}>↗</button
                          ></td
                        ></tr
                      >{/each}</tbody
                  >
                </table>
              </div>
              <div class="table-footer">
                <span>Arrastra más archivos PDF aquí</span
                >{#if catalog.next}<button
                    class="secondary"
                    onclick={() => load(true)}>Cargar más</button
                  >{/if}
              </div>
            {:else}<div class="empty">
                <span class="empty-icon">▤</span>
                <h2>
                  {query
                    ? "No hay documentos con ese nombre"
                    : filter === "pending"
                      ? "No hay documentos en procesamiento"
                      : filter === "failed"
                        ? "Todo en orden"
                        : "Tu próxima idea empieza aquí"}
                </h2>
                <p>
                  {filter === "all" && !query
                    ? "Arrastra tus archivos PDF o selecciona documentos para comenzar."
                    : "Los documentos aparecerán aquí cuando coincidan con este filtro."}
                </p>
                {#if filter === "all" && !query}<button
                    class="secondary"
                    onclick={() => fileInput.click()}
                    >Seleccionar documentos</button
                  ><small
                    >Podrás abrirlos mientras se prepara la búsqueda.</small
                  >{/if}
              </div>{/if}
          </section>
        {/if}
      {/if}
      {#if error}<div class="error" role="alert">
          {error}<button class="quiet" onclick={() => (error = "")}
            >Cerrar</button
          >
        </div>{/if}
      {#if sessionReady}
        <details class="privacy-note">
          <summary>Sobre este espacio y la medición de uso</summary>
          <p>
            {#if accessMode === "account"}
              Tu biblioteca está vinculada a tu cuenta. Cerrar sesión conserva
              los documentos.
            {:else if accessMode === "ephemeral"}
              Espacio temporal de este navegador: caduca en aproximadamente {Math.max(
                1,
                Math.ceil(expiresIn / 3600),
              )} horas. Al caducar se elimina automáticamente; «Terminar y borrar
              espacio» adelanta la eliminación. Borrar cookies impide volver a abrirlo.
              Cerrar la pestaña no adelanta su caducidad.
            {:else}
              Este espacio sin cuenta conserva su política anterior. Los
              archivos existentes no se eliminan automáticamente.
            {/if}
          </p>
          <p>
            Para mejorar SARA registramos acciones, tiempos y errores durante {retentionDays}
            días. No incluimos documentos, nombres de archivo, preguntas, respuestas
            ni direcciones IP en estas métricas.
          </p>
          <button
            class="secondary"
            role="switch"
            aria-checked={measurement}
            disabled={savingPreference || !measurementAvailable}
            onclick={changeMeasurement}
            >{measurement
              ? "Medición de uso activada"
              : "Medición de uso desactivada"}</button
          >
        </details>
      {/if}
      <div class="bottom-note">
        <span>SARA DocReader</span><span
          >Un espacio para leer, conectar y descubrir.</span
        >
      </div>
    </main>
  </div>
</div>
{#if selected}
  <div class="reader-shell">
    <header class="reader-heading">
      <button
        class="secondary"
        onclick={() => {
          selected = null;
          chatController?.abort();
        }}>← Biblioteca</button
      ><strong>{selected.name}</strong><span class={`status ${selected.status}`}
        >{states[selected.status]}</span
      >
    </header>
    <div class="reader-body">
      <section class="reader-document" aria-label="Visor del documento">
        {#key selected.id}<PdfViewer id={selected.id} bind:page />{/key}
      </section>
      <aside class="reader-panel">
        {#if error}<p class="error" role="alert">{error}</p>{/if}
        <div class="tabs">
          <button
            class:active={panel === "info"}
            onclick={() => (panel = "info")}>Documento</button
          ><button
            class:active={panel === "chat"}
            onclick={() => (panel = "chat")}>Preguntar</button
          >
        </div>
        {#if panel === "info"}<span class="eyebrow">DETALLES DEL ARCHIVO</span>
          <h2>{selected.name}</h2>
          <dl>
            <dt>Páginas</dt>
            <dd>{selected.page_count ?? "Por determinar"}</dd>
            <dt>Tamaño</dt>
            <dd>{bytes(selected.size)}</dd>
            <dt>Estado</dt>
            <dd>{states[selected.status]}</dd>
            {#if selected.category}<dt>Categoría sugerida</dt>
              <dd>{selected.category}</dd>{/if}
          </dl>
          {#if selected.summary}<h3>Síntesis del documento</h3>
            <div class="answer">
              {selected.summary}
            </div>{/if}{#if selected.summary_error}<p class="error">
              {selected.summary_error}
            </p>{/if}<button
            class="secondary"
            disabled={selected.status !== "ready"}
            onclick={generateSummary}
            >{selected.summary
              ? "Actualizar síntesis"
              : "Generar síntesis y categoría"}</button
          >
          <p>
            El original está disponible desde el momento en que lo subes. La
            búsqueda se prepara en segundo plano.
          </p>
          {#if selected.error}<p class="error">
              {selected.error}
            </p>{/if}{#if selected.embedding_error}<p class="notice">
              {selected.embedding_error}
            </p>{/if}{#if selected.status === "failed" || selected.embedding_error}<button
              class="secondary"
              onclick={() => retry(selected!)}>Reintentar procesamiento</button
            >{/if}<a
            class="button secondary"
            href={`/api/documents/${selected.id}/file`}
            target="_blank"
            rel="noreferrer">Abrir original ↗</a
          >
        {:else}<span class="eyebrow">CONSULTA CON FUENTES</span>
          <h2>Pregúntale al documento</h2>
          <p>
            Las respuestas se apoyan en fragmentos del archivo. Revisa siempre
            la fuente.
          </p>
          <details class="extra-documents">
            <summary>Incluir otros documentos ({extraDocs.length})</summary
            >{#each catalog.items.filter((d) => d.id !== selected?.id && d.status === "ready") as doc}<label
                ><input
                  type="checkbox"
                  value={doc.id}
                  bind:group={extraDocs}
                  disabled={!extraDocs.includes(doc.id) &&
                    extraDocs.length >= 19}
                />{doc.name}</label
              >{/each}<small
              >Documentos disponibles en la lista cargada de tu biblioteca.</small
            >
          </details>
          <form
            onsubmit={(e) => {
              e.preventDefault();
              void ask();
            }}
          >
            <label for="question">Tu pregunta</label><textarea
              id="question"
              placeholder="¿Qué metodología se utilizó?"
              bind:value={question}
              maxlength="2000"
              rows="4"></textarea><button
              class="primary"
              disabled={asking ||
                selected.status !== "ready" ||
                !question.trim()}
              >{asking ? "Consultando…" : "Consultar documento →"}</button
            >{#if asking}<button
                type="button"
                class="quiet"
                onclick={() => chatController?.abort()}>Detener</button
              >{/if}
          </form>
          {#if selected.status !== "ready"}<p class="notice">
              Podrás consultar cuando termine el procesamiento.
            </p>{/if}{#if chatError}<p class="error" role="alert">
              {chatError}
            </p>{/if}{#if answer}<div class="answer">
              {answer}
            </div>
            <div class="answer-feedback">
              <span>¿Te resultó útil?</span><button
                class="secondary"
                disabled={feedback !== null || asking}
                onclick={() => rateAnswer("positive")}>Sí</button
              ><button
                class="secondary"
                disabled={feedback !== null || asking}
                onclick={() => rateAnswer("negative")}>No</button
              >{#if feedback}<small>Gracias por tu valoración.</small>{/if}
            </div>{/if}{#if sources.length}<h3>Fuentes consultadas</h3>
            {#each sources as source, i}<button
                class="source"
                onclick={() => openSource(source)}
                ><strong>Fuente {i + 1} · Página {source.page} ↗</strong><span
                  >{source.name}</span
                ><span>{source.text.slice(0, 150)}…</span></button
              >{/each}{/if}{/if}
      </aside>
    </div>
  </div>
{/if}

<style>
  .access-options {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1.25rem;
    margin: 2rem 0;
  }
  .access-options article {
    border: 1px solid var(--line, #c9c9c9);
    border-radius: 1rem;
    padding: 1.5rem;
  }
  .access-options h2 {
    font-size: 1.25rem;
  }
  .access-options small {
    display: block;
    margin-top: 1rem;
  }
  @media (max-width: 700px) {
    .access-options {
      grid-template-columns: 1fr;
    }
  }
</style>
