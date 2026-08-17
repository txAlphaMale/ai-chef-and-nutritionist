import { useCallback, useEffect, useRef, useState } from "react";
import { BrowserCodeReader, BrowserMultiFormatReader } from "@zxing/browser";

// Camera barcode scanner, using ZXing -- a pure-JS decoder that runs
// entirely in the browser, so only the decoded number is sent to the
// backend (GET /api/inventory/barcode-lookup).
//
// ZXing is the only implementation path here, not "native BarcodeDetector
// with a ZXing fallback". Safari and every browser on iOS/iPadOS (WebKit
// is the only engine Apple permits, so this covers "Chrome for iOS" and
// "Firefox for iOS" too) do not implement BarcodeDetector. Branching on
// native availability would put iOS on a second, far-less-exercised path
// for no benefit, so every platform uses ZXing.
//
// A desktop webcam can decode a UPC-A/EAN-13 grocery barcode, just less
// reliably than a phone at close range -- a physical limitation, not
// something this component can code around. The manual entry field below
// exists for that case, and for a barcode too worn to scan at all.
export default function BarcodeScanner({ onDetected, onClose, paused = false }) {
  const videoRef = useRef(null);
  const readerRef = useRef(null);
  const controlsRef = useRef(null);
  const streamRef = useRef(null);
  const [devices, setDevices] = useState([]);
  const [deviceId, setDeviceId] = useState("");
  const [showPicker, setShowPicker] = useState(false);
  const [error, setError] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [manualBarcode, setManualBarcode] = useState("");

  // `onDetected` is held in a ref rather than depended on directly. The
  // parent recreates that callback every render, so depending on it tore
  // the camera down and restarted it on every parent state change --
  // visible as a flickering preview, and on slower devices a scanner
  // that never stayed up long enough to decode anything.
  const onDetectedRef = useRef(onDetected);
  useEffect(() => {
    onDetectedRef.current = onDetected;
  }, [onDetected]);

  // `paused` goes through a ref for the same reason `onDetected` does, and
  // it matters more here: the ZXing frame callback below is created once,
  // when decoding starts, so it would capture the value of `paused` at
  // that moment and never see it change. Reading a ref inside the callback
  // is what makes pausing work at all without tearing the camera down.
  const pausedRef = useRef(paused);
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  const stopEverything = useCallback(() => {
    // controls.stop() disposes the stream ZXing was given, but it only
    // exists once decoding actually started. Stopping the raw stream too
    // covers the window before that, where a quick cancel would
    // otherwise leave the camera light on.
    controlsRef.current?.stop();
    controlsRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  }, []);

  // One camera acquisition, not two.
  //
  // WebKit withholds device identity until camera access has been
  // granted: on an unpermissioned origin `enumerateDevices()` returns
  // entries with empty labels and empty/obfuscated ids. So permission has
  // to come first, and enumeration second -- gating the stream on a
  // device id means `getUserMedia` is never called at all and the camera
  // silently never engages.
  //
  // The stream opened for that permission step is then handed straight to
  // `decodeFromStream`, rather than released so ZXing can open its own.
  // Two acquisitions per scan is a second camera start, and on WebKit a
  // second opportunity to prompt.
  useEffect(() => {
    // Checked first and unconditionally: WebKit keeps
    // `navigator.mediaDevices.getUserMedia` PRESENT on an insecure origin
    // but never settles calls made through it, so feature detection
    // passes and then hangs forever with nothing shown.
    if (window.isSecureContext === false) {
      setError(
        "Camera access needs HTTPS (or localhost) -- this page was loaded over a plain, non-secure connection. Type the barcode number below instead."
      );
      return undefined;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("This browser doesn't support camera access. Type the barcode number below instead.");
      return undefined;
    }

    let cancelled = false;
    readerRef.current = readerRef.current || new BrowserMultiFormatReader();

    async function start() {
      setError(null);
      setScanning(false);

      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          // `ideal`, not `exact`, for facingMode: a laptop with only a
          // front-facing webcam must still get a stream rather than an
          // OverconstrainedError. An explicitly chosen device IS exact --
          // if the user picked a camera, silently using a different one
          // is worse than failing.
          video: deviceId ? { deviceId: { exact: deviceId } } : { facingMode: { ideal: "environment" } },
        });
      } catch (err) {
        if (!cancelled) setError(cameraErrorMessage(err));
        return;
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop());
        return;
      }
      streamRef.current = stream;

      // Labels are readable now that permission is granted. Only needed
      // to populate the optional picker, so a failure here is not fatal.
      try {
        const list = await BrowserCodeReader.listVideoInputDevices();
        if (!cancelled) setDevices(list);
      } catch {
        /* picker stays empty; the default camera is already streaming */
      }
      if (cancelled) return;

      try {
        const controls = await readerRef.current.decodeFromStream(stream, videoRef.current, (result, err) => {
          if (result) {
            // Paused means "a scan is being reviewed upstairs" -- keep
            // decoding frames but ignore them, so the camera does not have
            // to be torn down and warmed back up between items. Camera
            // start-up is 1-3 seconds on a phone, which is the dominant
            // cost when adding a shelf of packaged goods one barcode at a
            // time (capstone review 2026-08-16).
            if (pausedRef.current) return;
            onDetectedRef.current(result.getText());
            return;
          }
          // ZXing invokes this on every frame, decoded or not. A frame
          // with no readable barcode is the normal state while hunting
          // for one -- only a genuine stream/device failure is worth
          // surfacing.
          if (err && !isScanMiss(err)) setError(cameraErrorMessage(err));
        });
        if (cancelled) {
          controls.stop();
          return;
        }
        controlsRef.current = controls;
        setScanning(true);
      } catch (err) {
        if (!cancelled) setError(cameraErrorMessage(err));
      }
    }

    start();

    return () => {
      cancelled = true;
      stopEverything();
    };
  }, [deviceId, stopEverything]);

  function handleManualSubmit(e) {
    e.preventDefault();
    if (!manualBarcode.trim()) return;
    // Deliberately does NOT stop the camera any more. The scanner now has
    // exactly one lifecycle rule -- it stays up until you close it -- and
    // typing one worn barcode by hand in the middle of a run of scans
    // should not end the run.
    onDetected(manualBarcode.trim());
    setManualBarcode("");
  }

  function handleClose() {
    stopEverything();
    onClose();
  }

  return (
    <div className="barcode-scanner">
      {!error && (
        <div className="barcode-scanner-video-wrap">
          {/* Mounted before decoding starts, not after: decodeFromStream
              needs this element to attach the stream to. */}
          <video ref={videoRef} className="barcode-scanner-video" muted playsInline autoPlay />
        </div>
      )}
      {!error && !scanning && <p className="hint">Starting the camera...</p>}
      {error && <p className="error-text">{error}</p>}
      {!error && scanning && (
        <p className="hint">Point the camera at a barcode -- it scans automatically, no button to press.</p>
      )}

      {/* Hidden behind a toggle. `facingMode: environment` already picks
          the right camera, and a phone enumerates every physical lens
          (triple, ultra wide, telephoto...) -- a list that is noise
          unless the default actually picked wrong. */}
      {!error && devices.length > 1 && (
        <div className="barcode-scanner-device">
          {showPicker ? (
            <label>
              Camera
              <select value={deviceId} onChange={(e) => setDeviceId(e.target.value)}>
                <option value="">Automatic (rear camera)</option>
                {devices.map((d, i) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || `Camera ${i + 1}`}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setShowPicker(true)}>
              Use a different camera
            </button>
          )}
        </div>
      )}

      <form className="barcode-scanner-manual" onSubmit={handleManualSubmit}>
        <input
          placeholder="Or type the barcode number"
          aria-label="Barcode number"
          value={manualBarcode}
          onChange={(e) => setManualBarcode(e.target.value)}
          inputMode="numeric"
        />
        <button type="submit" className="btn btn-secondary btn-sm" disabled={!manualBarcode.trim()}>
          Use this number
        </button>
      </form>
      <div className="form-actions">
        <button type="button" className="btn btn-secondary" onClick={handleClose}>
          Cancel
        </button>
      </div>
    </div>
  );
}

// Errors ZXing raises per-frame that mean "nothing readable here", not
// "the camera broke".
const SCAN_MISS_KINDS = new Set(["NotFoundException", "ChecksumException", "FormatException"]);

// Classify by ZXing's own `kind`, NEVER by `err.name`.
//
// @zxing/library's exceptions do not set `.name` at all. It comes from
// ts-custom-error, which assigns `new.target.name` -- the constructor's
// function name, which a production minifier rewrites to something like
// "e". So `err.name === "NotFoundException"` is true in dev and false in
// every built bundle, which turned the normal no-barcode-in-this-frame
// case into a fatal "Could not start the camera" on the first frame and
// made the scanner unusable in the container while testing clean in dev.
//
// `kind` is a static string literal on each exception class, so it
// survives minification intact.
function zxingKind(err) {
  if (!err) return null;
  if (typeof err.getKind === "function") {
    try {
      return err.getKind();
    } catch {
      /* fall through to the static */
    }
  }
  return err.constructor?.kind ?? null;
}

function isScanMiss(err) {
  return SCAN_MISS_KINDS.has(zxingKind(err));
}

function cameraErrorMessage(err) {
  if (err?.name === "NotAllowedError") {
    return "Camera access was denied. Allow camera access in your browser's settings, or type the barcode number below instead.";
  }
  if (err?.name === "NotFoundError" || err?.name === "OverconstrainedError") {
    return "No camera found on this device. Type the barcode number below instead.";
  }
  if (err?.name === "NotReadableError") {
    // Another app (or another tab) already holds the camera. Worth its own
    // message: "could not start the camera" sends people to their
    // permission settings, which is not the problem.
    return "The camera is in use by another app or tab. Close it and try again, or type the barcode number below.";
  }
  return `Could not start the camera (${err?.message || err}). Type the barcode number below instead.`;
}
