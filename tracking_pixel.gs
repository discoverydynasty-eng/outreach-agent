/**
 * tracking_pixel.gs - open-tracking endpoint, as a Google Apps Script web app.
 *
 * Chosen over Cloudflare Workers / Val.town / a VPS because it needs no new account, no
 * credit card and no deploy tooling: it runs on the Google account you already have, and
 * the log lands in a Google Sheet you can open and read directly. It also serves from
 * script.google.com, a domain mail filters already trust - which matters, because the
 * whole reason to be careful here is that adding a remote image is the main deliverability
 * risk in this system.
 *
 * It never sees an email address. The pixel URL carries only an opaque HMAC token; the
 * token -> recipient mapping stays local in open_tokens.json on the sending machine.
 *
 * ── DEPLOY (about 3 minutes) ──────────────────────────────────────────────────────────
 *  1. Create a Google Sheet. Name the first tab exactly: opens
 *  2. In that Sheet: Extensions > Apps Script. Delete the placeholder, paste this file.
 *  3. Deploy > New deployment > type "Web app".
 *       Execute as:        Me
 *       Who has access:    Anyone            <- REQUIRED, mail clients fetch anonymously
 *  4. Authorise when prompted, then copy the /exec Web app URL.
 *  5. Put that URL in config.json as "open_tracking_url", then send a test to yourself.
 *
 * Sanity check: open <your-exec-url>?t=selftest in a browser - a row should appear in the
 * Sheet. And <your-exec-url>?json=1 should return JSON.
 */

var SHEET_NAME = 'opens';

// 1x1 transparent GIF.
var PIXEL_B64 = 'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

function doGet(e) {
  var params = (e && e.parameter) || {};

  if (params.json) {
    return ContentService
      .createTextOutput(JSON.stringify({ opens: collectOpens() }))
      .setMimeType(ContentService.MimeType.JSON);
  }

  var token = (params.t || '').toString().slice(0, 64);
  if (token) {
    try {
      logOpen(token, e);
    } catch (err) {
      // Never let a logging failure stop the image from being served - a broken image in
      // a prospect's inbox would be far more damaging than a missed data point.
    }
  }

  return HtmlService.createHtmlOutput(
    '<img src="data:image/gif;base64,' + PIXEL_B64 + '" width="1" height="1">'
  ).setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function sheet_() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(['token', 'timestamp_iso', 'user_agent']);
  }
  return sh;
}

function logOpen(token, e) {
  var ua = '';
  try {
    // Useful for spotting proxy-driven opens: Gmail pre-caches images through
    // GoogleImageProxy and Apple Mail Privacy Protection loads them unconditionally, so a
    // large share of "opens" are machines, not people. Recording the agent lets you tell.
    ua = (e && e.parameter && e.parameter.ua) || '';
  } catch (err) {}
  sheet_().appendRow([token, new Date().toISOString(), ua]);
}

function collectOpens() {
  var sh = sheet_();
  var last = sh.getLastRow();
  if (last < 2) return {};
  var rows = sh.getRange(2, 1, last - 1, 2).getValues();
  var out = {};
  for (var i = 0; i < rows.length; i++) {
    var tok = String(rows[i][0] || '').trim();
    if (!tok) continue;
    var ts = rows[i][1];
    var iso = (ts instanceof Date) ? ts.toISOString() : String(ts);
    if (!out[tok]) {
      out[tok] = { first: iso, count: 1 };
    } else {
      out[tok].count += 1;
      if (iso < out[tok].first) out[tok].first = iso;
    }
  }
  return out;
}
