package com.cinesip.recap

import android.content.Intent
import android.webkit.JavascriptInterface
import androidx.core.content.FileProvider
import com.cinesip.recap.ai.AiManager
import com.cinesip.recap.pipeline.RecapPipeline
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.io.File

/** JS bridge — Zippy's AndroidBridge pattern, extended for on-device AI. */
class CinesipBridge(private val act: MainActivity) {
    private val scope = CoroutineScope(Dispatchers.Main)

    @JavascriptInterface fun toast(m: String) = act.toast(m)

    @JavascriptInterface fun pickVideo(): String {
        act.openPicker()
        return ""
    }
    @JavascriptInterface fun pickAudio(): String {
        act.openAudioPicker()
        return ""
    }
    @JavascriptInterface fun getPickedPath(): String = act.getPicked() ?: ""

    /** Check/download AI models. JS calls this on startup. */
    @JavascriptInterface
    fun ensureAiModels() {
        scope.launch(Dispatchers.IO) {
            val caps = AiManager.capabilities(act)
            act.runOnUiThread {
                act.webEval("onAiStatus(${caps["transcribe"]},${caps["faces"]},${caps["audio_events"]})")
            }
            if (caps.values.any { !it }) {
                AiManager.ensureModels(act) { name, pct ->
                    act.runOnUiThread { act.webEval("onModelProgress('$name',$pct)") }
                }
                val caps2 = AiManager.capabilities(act)
                act.runOnUiThread {
                    act.webEval("onAiStatus(${caps2["transcribe"]},${caps2["faces"]},${caps2["audio_events"]})")
                }
            }
        }
    }

    /**
     * Full on-device recap: transcribe → faces → audio → Gemini script
     * → SmartSync → TTS → render. 100% phone, no laptop.
     * Editor version: populates timeline, does NOT auto-render.
     */
    @JavascriptInterface
    fun startAutoRecap() {
        val srcPath = act.getPicked() ?: ""
        if (srcPath.isEmpty()) {
            act.runOnUiThread { act.webEval("onRecapError('ဗီဒီယို အရင် ရွေးပါ')") }
            return
        }
        startAutoRecap(srcPath, "full_story", 10, act.prefs("gemini_key") ?: "")
    }

    @JavascriptInterface
    fun startAutoRecap(srcPath: String, style: String,
                       targetMinutes: Int, geminiKey: String) {
        scope.launch(Dispatchers.IO) {
            try {
                val pipeline = RecapPipeline(act)
                // Run pipeline but capture intermediate results for editor
                val result = pipeline.runForEditor(
                    srcVideo = srcPath.ifEmpty { act.getPicked() ?: "" },
                    style = style,
                    targetMinutes = targetMinutes,
                    geminiKey = geminiKey.ifEmpty {
                        act.prefs("gemini_key") ?: ""
                    }
                ) { stage, pct ->
                    act.runOnUiThread {
                        act.webEval("onRecapProgress($pct,'$stage')")
                    }
                }
                act.runOnUiThread { act.webEval("onRecapDone('$result')") }
            } catch (e: Exception) {
                act.runOnUiThread {
                    act.webEval("onRecapError('${e.message?.take(100)?.replace("'", "")}')")
                }
            }
        }
    }

    /** Manual render (Zippy-style): single clip + audio mux. */
    @JavascriptInterface
    fun startRender(cfgJson: String) {
        act.toast("Render starting…")
        RenderWorker.run(act, cfgJson,
            onProg = { p, s -> act.onProgress(p, s) },
            onDone = { path -> act.onRenderDone(path) })
    }

    @JavascriptInterface fun shareOutput(): Boolean {
        val p = RenderWorker.lastOutput ?: return false
        val uri = FileProvider.getUriForFile(
            act, "${act.packageName}.provider", File(p))
        val i = Intent(Intent.ACTION_SEND).apply {
            type = "video/mp4"; putExtra(Intent.EXTRA_STREAM, uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        act.startActivity(Intent.createChooser(i, "Share recap"))
        return true
    }

    @JavascriptInterface fun saveGeminiKey(k: String) {
        act.prefs("gemini_key", k)
        act.toast("✓ Key သိမ်းပြီးပြီ")
    }
    @JavascriptInterface fun getGeminiKey(): String =
        act.prefs("gemini_key") ?: ""

    @JavascriptInterface fun haptic() {
        act.window?.decorView?.performHapticFeedback(
            android.view.HapticFeedbackConstants.VIRTUAL_KEY)
    }

    @JavascriptInterface fun copyText(t: String) {
        val cm = act.getSystemService(android.content.Context.CLIPBOARD_SERVICE)
            as android.content.ClipboardManager
        cm.setPrimaryClip(android.content.ClipData.newPlainText("cinesip", t))
        act.toast("✓ ကူးပြီးပြီ")
    }

    @JavascriptInterface fun appVersion(): String = BuildConfig.VERSION_NAME
}
