const sanitizeValue = (value: unknown) => {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
};

const normalizeRows = (rows: Record<string, unknown>[]) =>
  rows.map((row) => Object.fromEntries(Object.entries(row).map(([k, v]) => [k, sanitizeValue(v)])));

const escapeHtml = (value: string) =>
  value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

const downloadBlob = (filename: string, blob: Blob) => {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

export function exportRowsToExcel(filename: string, _sheetName: string, rows: Record<string, unknown>[]) {
  const safeRows = normalizeRows(rows);
  const headers = safeRows.length ? Object.keys(safeRows[0]) : ["Message"];
  const bodyRows = safeRows.length ? safeRows : [{ Message: "No data available" }];

  const tableHtml = `
    <table>
      <thead>
        <tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>
      </thead>
      <tbody>
        ${bodyRows
          .map((row) => `<tr>${headers.map((header) => `<td>${escapeHtml(String(row[header] ?? ""))}</td>`).join("")}</tr>`)
          .join("")}
      </tbody>
    </table>
  `;

  const html = `<!DOCTYPE html><html><head><meta charset="utf-8" /></head><body>${tableHtml}</body></html>`;
  const blob = new Blob(["\ufeff", html], { type: "application/vnd.ms-excel;charset=utf-8;" });
  const outputName = filename.replace(/\.xlsx$/i, ".xls");
  downloadBlob(outputName, blob);
}

export function exportRowsToPdf(title: string, _filename: string, rows: Record<string, unknown>[]) {
  const safeRows = normalizeRows(rows);
  const headers = safeRows.length ? Object.keys(safeRows[0]) : ["Message"];
  const bodyRows = safeRows.length ? safeRows : [{ Message: "No data available" }];

  const tableHtml = `
    <table>
      <thead>
        <tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>
      </thead>
      <tbody>
        ${bodyRows
          .map((row) => `<tr>${headers.map((header) => `<td>${escapeHtml(String(row[header] ?? ""))}</td>`).join("")}</tr>`)
          .join("")}
      </tbody>
    </table>
  `;

  const popup = window.open("", "_blank", "noopener,noreferrer,width=1200,height=800");
  if (!popup) {
    window.alert("Please allow pop-ups to export PDF.");
    return;
  }

  popup.document.write(`<!DOCTYPE html>
    <html>
      <head>
        <meta charset="utf-8" />
        <title>${escapeHtml(title)}</title>
        <style>
          body { font-family: Arial, sans-serif; padding: 24px; color: #0f172a; }
          h1 { font-size: 20px; margin-bottom: 16px; }
          table { border-collapse: collapse; width: 100%; }
          th, td { border: 1px solid #cbd5e1; padding: 8px; font-size: 12px; text-align: left; vertical-align: top; }
          th { background: #eff6ff; }
        </style>
      </head>
      <body>
        <h1>${escapeHtml(title)}</h1>
        ${tableHtml}
      </body>
    </html>`);
  popup.document.close();
  setTimeout(() => {
    popup.focus();
    popup.print();
  }, 250);
}
