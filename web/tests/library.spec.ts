import { test, expect } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";

// Browser harness uses real persisted anonymous identities. Gateway cookie/CSRF
// boundary is covered by Rust tests and a separate compose smoke test.
async function anonymous(page: any) {
  const response = await page.request.post(
    "http://127.0.0.1:8046/internal/anonymous/session",
    { data: { create: true } },
  );
  const session = await response.json();
  const subject = session.subject;
  await page.context().addCookies([
    {
      name: "sara_visitor",
      value: session.token,
      url: "http://127.0.0.1:4173",
      httpOnly: true,
      sameSite: "Lax",
    },
    {
      name: "smartdoc_csrf",
      value: randomUUID(),
      url: "http://127.0.0.1:4173",
    },
  ]);
  await page.route("**/session/me", (route: any) =>
    route.fulfill({
      json: {
        anonymous: true,
        mode: "ephemeral",
        expires_in: 86400,
        login_available: false,
      },
    }),
  );
  await page.route("**/api/**", async (route: any) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({
      url: `http://127.0.0.1:8046${url.pathname}${url.search}`,
      headers: { ...route.request().headers(), "x-smartdoc-subject": subject },
    });
    await route.fulfill({ response });
  });
}

test("anonymous library opens directly and measurement can be disabled", async ({
  page,
}) => {
  await anonymous(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Mi biblioteca" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /Entrar con/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Cerrar sesión" })).toHaveCount(
    0,
  );
  await page.getByText("Sobre este espacio y la medición de uso").click();
  const measurement = page.getByRole("switch");
  await expect(measurement).toHaveAttribute("aria-checked", "true");
  await measurement.click();
  await expect(measurement).toHaveAttribute("aria-checked", "false");
  await page.reload();
  await page.getByText("Sobre este espacio y la medición de uso").click();
  await expect(measurement).toHaveAttribute("aria-checked", "false");
  await page.screenshot({
    path: "test-results/sara-anonymous-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/sara-anonymous-mobile.png",
    fullPage: true,
  });
});

test("upload, asynchronous indexing, content search and PDF page render", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await anonymous(page);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Mi biblioteca" }),
  ).toBeVisible();
  const pdf = execFileSync("../.venv/bin/python", [
    "-c",
    "import pymupdf,sys; d=pymupdf.open(); p=d.new_page(); p.insert_text((50,60),'Espectroscopia: protocolo de calibracion del laboratorio.'); p=d.new_page(); p.insert_text((50,60),'Segunda pagina: resultados de sensores.'); sys.stdout.buffer.write(d.tobytes())",
  ]);
  await page.getByLabel("Seleccionar documentos PDF").setInputFiles({
    name: "Protocolo de espectroscopia.pdf",
    mimeType: "application/pdf",
    buffer: pdf,
  });
  await expect(page.getByText("Guardado", { exact: true })).toBeVisible();
  await expect(page.getByText("Listo", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await page.screenshot({
    path: "test-results/sara-library-desktop.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "PDF Protocolo de espectroscopia.pdf" })
    .click();
  await expect(page.getByText("Página 1 de 2")).toBeVisible();
  await expect
    .poll(() =>
      page.locator("canvas").evaluate((el: HTMLCanvasElement) => el.width),
    )
    .toBeGreaterThan(0);
  await page.getByRole("button", { name: "Página siguiente" }).click();
  await expect(page.getByText("Página 2 de 2")).toBeVisible();
  await page.screenshot({
    path: "test-results/sara-reader-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "← Biblioteca" }).click();
  await page.getByLabel("Tipo de búsqueda").selectOption("text");
  await page
    .getByRole("textbox", { name: "Buscar documentos" })
    .fill("espectroscopia");
  await expect(page.getByRole("button", { name: /PÁGINA 1/ })).toBeVisible();
  await page.getByRole("button", { name: /PÁGINA 1/ }).click();
  await expect(page.getByText("Página 1 de 2")).toBeVisible();
  await page.getByRole("button", { name: "Preguntar", exact: true }).click();
  await page.getByLabel("Tu pregunta").fill("espectroscopia");
  await page.getByRole("button", { name: "Consultar documento →" }).click();
  await expect(
    page.getByText("El chat aún no está configurado.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "← Biblioteca" }).click();
  await page.getByRole("button", { name: "Limpiar búsqueda" }).click();
  await page.getByRole("button", { name: "Tema oscuro" }).click();
  await page.screenshot({
    path: "test-results/sara-library-dark.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/sara-library-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("first visit offers temporary access and keeps central login disabled", async ({
  page,
}) => {
  await page.route("**/session/me", (route) =>
    route.fulfill({
      json: { mode: "choose", login_available: false, ephemeral_hours: 24 },
    }),
  );
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Tu biblioteca, a tu manera." }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Iniciar sesión · próximamente" }),
  ).toBeDisabled();
  await expect(page.getByText(/después de 24 horas/)).toBeVisible();
  await page.screenshot({
    path: "test-results/access-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Usar temporalmente →" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "test-results/access-mobile.png",
    fullPage: true,
  });
  await anonymous(page);
  await page.route("**/session/anonymous", (route) =>
    route.fulfill({
      json: {
        mode: "ephemeral",
        anonymous: true,
        expires_in: 86400,
        login_available: false,
      },
    }),
  );
  await page.getByRole("button", { name: "Usar temporalmente →" }).click();
  await expect(
    page.getByRole("heading", { name: "Mi biblioteca" }),
  ).toBeVisible();
  await page.route("**/session/end", (route) => route.fulfill({ json: {} }));
  await page.route("**/session/me", (route) =>
    route.fulfill({ json: { mode: "choose", login_available: false } }),
  );
  await page.getByRole("button", { name: "Terminar y borrar espacio" }).click();
  await expect(
    page.getByRole("heading", { name: "Tu biblioteca, a tu manera." }),
  ).toBeVisible();
});
