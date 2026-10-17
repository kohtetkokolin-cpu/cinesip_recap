package com.cinesip.recap

import com.antonkarpenko.ffmpegkit.FFmpegKit
import com.antonkarpenko.ffmpegkit.FFprobeKit
import com.antonkarpenko.ffmpegkit.ReturnCode
import java.io.File
import java.util.concurrent.Executors

/** On-device render via FFmpegKit (same approach as Zippy). */
object RenderWorker {
    var lastOutput: String? = null
    private val ex = Executors.newSingleThreadExecutor()

    fun run(act: MainActivity, cfgJson: String,
            onProg: (Int, String) -> Unit,
            onDone: (String) -> Unit) {
        ex.execute {
            try {
                val cfg = org.json.JSONObject(cfgJson)
                val src = cfg.getString("src")
                val audio = cfg.optString("audio", "")
                val out = File(act.getExternalFilesDir(null),
                    "recap_${System.currentTimeMillis()}.mp4").absolutePath
                // Phase-1 render: concat-safe transcode + audio mux + loudnorm.
                // (AutoSync clip graph + overlays land in Phase 2.)
                val vf = "scale=1280:-2,setsar=1,fps=30"
                val cmd = if (audio.isNotEmpty())
                    "-y -i \"$src\" -i \"$audio\" -vf \"$vf\" " +
                    "-c:v libx264 -preset veryfast -crf 22 " +
                    "-c:a aac -b:a 128k -af loudnorm=I=-16:TP=-1.5:LRA=11 " +
                    "-shortest \"$out\""
                else
                    "-y -i \"$src\" -vf \"$vf\" -c:v libx264 " +
                    "-preset veryfast -crf 22 -an \"$out\""
                onProg(10, "rendering")
                val session = FFmpegKit.execute(cmd)
                if (ReturnCode.isSuccess(session.returnCode)) {
                    lastOutput = out
                    onProg(100, "done")
                    onDone(out)
                } else {
                    act.toast("Render failed: ${session.failStackTrace?.take(120)}")
                    onProg(0, "failed")
                }
            } catch (e: Exception) {
                act.toast("Render error: ${e.message?.take(80)}")
            }
        }
    }
}
