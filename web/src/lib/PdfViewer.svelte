<script lang="ts">
  import { onMount } from "svelte";
  import { track } from "$lib/analytics";
  import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
  let { id, page = $bindable(1) } = $props<{ id: string; page?: number }>();
  let canvas: HTMLCanvasElement;
  let host: HTMLDivElement;
  let pdf = $state<PDFDocumentProxy | null>(null);
  let loading = $state(true);
  let error = $state("");
  let width = $state(700);
  let renderTask: RenderTask | undefined;
  onMount(() => {
    let disposed = false;
    let destroy: (() => void) | undefined;
    const observer = new ResizeObserver((entries) => {
      width = Math.max(200, entries[0].contentRect.width - 32);
    });
    observer.observe(host);
    (async () => {
      try {
        const lib = await import("pdfjs-dist");
        lib.GlobalWorkerOptions.workerSrc = (
          await import("pdfjs-dist/build/pdf.worker.min.mjs?url")
        ).default;
        if (disposed) return;
        const task = lib.getDocument({ url: `/api/documents/${id}/file` });
        destroy = () => {
          void task.destroy();
        };
        const doc = await task.promise;
        if (!disposed) {
          pdf = doc;
          page = Math.min(Math.max(1, page), doc.numPages);
          loading = false;
        }
      } catch {
        if (!disposed) {
          error =
            "No se pudo abrir este PDF. Puedes descargarlo para revisarlo.";
          loading = false;
        }
      }
    })();
    return () => {
      disposed = true;
      observer.disconnect();
      renderTask?.cancel();
      destroy?.();
    };
  });
  $effect(() => {
    const doc = pdf,
      number = page,
      available = width;
    let cancelled = false;
    if (doc && canvas) {
      (async () => {
        renderTask?.cancel();
        try {
          const target = await doc.getPage(number);
          if (cancelled) return;
          const viewport = target.getViewport({
            scale: Math.min(
              available / target.getViewport({ scale: 1 }).width,
              1.6,
            ),
          });
          const ratio = Math.min(window.devicePixelRatio || 1, 2);
          canvas.width = Math.floor(viewport.width * ratio);
          canvas.height = Math.floor(viewport.height * ratio);
          canvas.style.width = `${viewport.width}px`;
          canvas.style.height = `${viewport.height}px`;
          renderTask = target.render({
            canvas,
            canvasContext: canvas.getContext("2d")!,
            viewport,
            transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
          });
          await renderTask.promise;
          if (!cancelled) track("page_view");
        } catch (e) {
          if (!cancelled && (e as Error).name !== "RenderingCancelledException")
            error = "No se pudo mostrar la página.";
        }
      })();
    }
    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  });
</script>

<div class="pdf-toolbar">
  <button
    class="icon-button"
    disabled={!pdf || page <= 1}
    onclick={() => page--}
    aria-label="Página anterior">←</button
  >
  <span>Página {page} de {pdf?.numPages ?? "…"}</span>
  <button
    class="icon-button"
    disabled={!pdf || page >= pdf.numPages}
    onclick={() => page++}
    aria-label="Página siguiente">→</button
  >
  <a
    class="button secondary"
    href={`/api/documents/${id}/file`}
    target="_blank"
    rel="noreferrer">Abrir original ↗</a
  >
</div>
<div class="pdf-stage" bind:this={host}>
  {#if loading}<p role="status">Abriendo documento…</p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <canvas bind:this={canvas} aria-label={`Página ${page} del PDF`}></canvas>
</div>
