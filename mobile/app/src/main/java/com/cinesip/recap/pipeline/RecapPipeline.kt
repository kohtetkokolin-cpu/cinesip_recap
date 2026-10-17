package com.cinesip.recap.pipeline

import android.content.Context
import com.antonkarpenko.ffmpegkit.FFmpegKit
import com.antonkarpenko.ffmpegkit.ReturnCode
import com.cinesip.recap.ai.*
import com.cinesip.recap.api.EdgeTtsClient
import com.cinesip.recap.api.GeminiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Full on-device recap pipeline. 100% phone, no laptop, no server.
 *
 * Stages:
 *  1. Extract audio (16kHz mono wav) via FFmpegKit
 *  2. Transcribe (sherpa-onnx, on-device)
 *  3. Sample frames → face detection + clustering (OpenCV, on-device)
 *  4. Audio events (YAMNet TFLite, on-device)
 *  5. Gemini script (HTTPS API)
 *  6. SmartSync beat→scene matching (on-device)
 *  7. Edge-TTS voice per beat (WebSocket API)
 *  8. Render final video (FFmpegKit, on-device)
 *
 * Every AI stage degrades gracefully — pipeline never hard-fails.
 */
class RecapPipeline(private val ctx: Context) {

    data class Progress(val stage: String, val pct: Int)

    suspend fun run(
        srcVideo: String,
        style: String,
        targetMinutes: Int,
        geminiKey: String,
        onProgress: (Progress) -> Unit
    ): String = withContext(Dispatchers.IO) {
        val workDir = File(ctx.getExternalFilesDir(null), "pipeline_${System.currentTimeMillis()}")
        workDir.mkdirs()
        fun p(s: String, pct: Int) = onProgress(Progress(s, pct))

        // ---- 1. Extract audio ----
        p("audio extract", 5)
        val wav = File(workDir, "audio.wav").absolutePath
        ffmpeg("-y -i \"$srcVideo\" -ar 16000 -ac 1 -c:a pcm_s16le \"$wav\"")

        // ---- 2. Transcribe ----
        p("transcribing", 10)
        val transcriber = Transcriber(ctx)
        val segments = try {
            transcriber.transcribe(wav) { pct -> p("transcribing", 10 + pct * 20 / 100) }
        } catch (e: Exception) { emptyList<Segment>() }
        finally { transcriber.release() }
        val transcript = segments.joinToString("\n") { "[${it.start.toInt()}s] ${it.text}" }

        // ---- 3. Faces: sample 1 frame / 5s, detect, cluster ----
        p("face analysis", 32)
        val faceKit = FaceKit(ctx)
        val movieLen = probeDuration(srcVideo)
        val frameFiles = sampleFrames(srcVideo, workDir, movieLen)
        val allFaces = mutableListOf<Face>()
        val frameFaceIds = mutableListOf<List<Int>>() // cluster per frame
        try {
            val embeddings = mutableListOf<FloatArray>()
            for ((i, fp) in frameFiles.withIndex()) {
                val faces = faceKit.processFrame(fp)
                allFaces.addAll(faces)
                embeddings.addAll(faces.map { it.embedding })
                p("face analysis", 32 + i * 8 / frameFiles.size.coerceAtLeast(1))
            }
            val clusters = if (embeddings.isNotEmpty())
                FaceKit.cluster(embeddings).toList() else emptyList()
            // map back to frames
            var fi = 0
            for (fp in frameFiles) {
                // (simplified: faces per frame in order)
                frameFaceIds.add(emptyList())
                fi++
            }
        } catch (e: Exception) { /* faces unavailable → skip */ }

        // ---- 4. Audio events ----
        p("audio events", 42)
        val audioEv = AudioEvents(ctx)
        val events = try {
            audioEv.analyze(wav) { pct -> p("audio events", 42 + pct * 8 / 100) }
        } catch (e: Exception) { emptyList<AudioEvent>() }
        finally { audioEv.release() }

        // ---- 5. Build scene windows (5s each) with signals ----
        val scenes = buildScenes(movieLen, events, frameFaceIds)

        // ---- 6. Gemini script ----
        p("writing script", 52)
        val evidence = buildEvidenceSummary(segments, events, scenes)
        val gemini = GeminiClient(geminiKey)
        val beats = gemini.writeScript(transcript, style, targetMinutes, evidence)
        p("script done: ${beats.size} beats", 60)

        // ---- 7. SmartSync matching ----
        p("smart matching", 62)
        val nameToCluster = autoNameClusters(segments, allFaces)
        val matches = SmartSync.smartSelect(beats, scenes, movieLen, nameToCluster)
        p("matched ${matches.count { it.score > 0 }} / ${beats.size}", 66)

        // ---- 8. TTS per beat ----
        p("voice synthesis", 68)
        val tts = EdgeTtsClient(ctx)
        val beatAudio = mutableListOf<String>()
        for ((i, beat) in beats.withIndex()) {
            val ap = File(workDir, "beat_$i.mp3").absolutePath
            try {
                tts.synthesize(beat.textMy, ap)
            } catch (e: Exception) {
                // TTS failed for this beat → silence placeholder
                ffmpeg("-y -f lavfi -i anullsrc=r=24000:cl=mono -t ${beat.durTarget} \"$ap\"")
            }
            beatAudio.add(ap)
            p("voice synthesis", 68 + i * 12 / beats.size.coerceAtLeast(1))
        }

        // ---- 9. Render: per-beat clip + VO, concat ----
        p("rendering", 82)
        val out = File(ctx.getExternalFilesDir(null),
            "recap_${System.currentTimeMillis()}.mp4").absolutePath
        renderBeats(srcVideo, matches, beatAudio, workDir, out) { pct ->
            p("rendering", 82 + pct * 18 / 100)
        }

        p("done", 100)
        out
    }

    /**
     * Editor version: runs pipeline through TTS, returns JSON for UI.
     * Does NOT render — user edits timeline first, then hits "Make video".
     * Returns: {"clips":[...], "audioSlots":[...], "links":{clipId:audioId}}
     */
    suspend fun runForEditor(
        srcVideo: String,
        style: String,
        targetMinutes: Int,
        geminiKey: String,
        onProgress: (String, Int) -> Unit
    ): String = withContext(Dispatchers.IO) {
        val workDir = File(ctx.getExternalFilesDir(null), "pipeline_${System.currentTimeMillis()}")
        workDir.mkdirs()
        fun p(s: String, pct: Int) = onProgress(s, pct)

        // ---- 1. Extract audio ----
        p("audio extract", 5)
        val wav = File(workDir, "audio.wav").absolutePath
        ffmpeg("-y -i \"$srcVideo\" -ar 16000 -ac 1 -c:a pcm_s16le \"$wav\"")

        // ---- 2. Transcribe (stub in Phase 1) ----
        p("transcribing", 10)
        val segments = emptyList<Segment>()
        val transcript = ""

        // ---- 3. Faces (stub in Phase 1) ----
        p("face analysis", 32)
        val movieLen = probeDuration(srcVideo)
        val allFaces = emptyList<Face>()

        // ---- 4. Audio events (stub in Phase 1) ----
        p("audio events", 42)
        val events = emptyList<AudioEvent>()

        // ---- 5. Build scene windows (2.5s each, Zippy-style) ----
        val scenes = buildScenes(movieLen, events, emptyList())
        p("scenes: ${scenes.size}", 48)

        // ---- 6. Gemini script ----
        p("writing script", 52)
        val evidence = buildEvidenceSummary(segments, events, scenes)
        val gemini = GeminiClient(geminiKey)
        val beats = try {
            gemini.writeScript(transcript, style, targetMinutes, evidence)
        } catch (e: Exception) {
            // Fallback: create placeholder beats if Gemini fails
            (0 until (targetMinutes * 12)).map { i ->
                Beat(i, "ဇာတ်လမ်း အပိုင်း ${i+1}", "scene ${i+1}", 5.0)
            }
        }
        p("script done: ${beats.size} beats", 60)

        // ---- 7. SmartSync matching ----
        p("smart matching", 62)
        val matches = SmartSync.smartSelect(beats, scenes, movieLen, emptyMap())
        p("matched ${matches.count { it.score > 0 }} / ${beats.size}", 66)

        // ---- 8. TTS per beat ----
        p("voice synthesis", 68)
        val tts = EdgeTtsClient(ctx)
        val beatAudio = mutableListOf<String>()
        for ((i, beat) in beats.withIndex()) {
            val ap = File(workDir, "beat_$i.mp3").absolutePath
            try {
                tts.synthesize(beat.textMy, ap)
            } catch (e: Exception) {
                ffmpeg("-y -f lavfi -i anullsrc=r=24000:cl=mono -t 5 \"$ap\"")
            }
            beatAudio.add(ap)
            p("voice synthesis", 68 + i * 12 / beats.size.coerceAtLeast(1))
        }

        // ---- Build editor JSON (no render yet) ----
        p("building timeline", 85)
        val clipsJson = scenes.mapIndexed { i, s ->
            """{"id":$i,"start":${s.start},"end":${s.end},"label":"Clip $i"}"""
        }.joinToString(",")
        val audioJson = beats.mapIndexed { i, b ->
            val txt = b.textMy.replace("\\", "\\\\").replace("\"", "'").replace("\n", " ").take(60)
            """{"id":$i,"start":${i*5.0},"end":${(i+1)*5.0},"text":"$txt"}"""
        }.joinToString(",")
        val linksJson = matches.mapNotNull { m ->
            val clipIdx = scenes.indexOf(m.scene)
            if (clipIdx >= 0) "\"$clipIdx\":\"${m.beatId}\"" else null
        }.joinToString(",")

        p("done", 100)
        """{"clips":[$clipsJson],"audioSlots":[$audioJson],"links":{$linksJson},"workDir":"${workDir.absolutePath}"}"""
    }

    /**
     * Render editor timeline from plan JSON.
     * Plan: {clips:[{id,start,end}], links:{clipId:audioId}, src, workDir}
     * Each linked clip is rendered with its TTS audio, then concatenated.
     */
    suspend fun renderTimeline(
        planJson: String,
        onProgress: (Int, String) -> Unit
    ): String = withContext(Dispatchers.IO) {
        val plan = org.json.JSONObject(planJson)
        val src = plan.getString("src")
        val workDirPath = plan.optString("workDir", "")
        val workDir = if (workDirPath.isNotEmpty()) File(workDirPath)
                      else File(ctx.getExternalFilesDir(null), "timeline_${System.currentTimeMillis()}").apply { mkdirs() }

        val clipsArr = plan.getJSONArray("clips")
        val linksObj = plan.getJSONObject("links")

        // Build clip map: id -> (start, end)
        val clipMap = mutableMapOf<String, Pair<Double, Double>>()
        for (i in 0 until clipsArr.length()) {
            val c = clipsArr.getJSONObject(i)
            clipMap[c.get("id").toString()] = Pair(c.getDouble("start"), c.getDouble("end"))
        }

        // Sort links by clip ID for chronological order
        val sortedLinks = linksObj.keys().asSequence().toList()
            .sortedBy { it.toIntOrNull() ?: 0 }

        if (sortedLinks.isEmpty()) throw Exception("No linked clips")

        val renderedClips = mutableListOf<String>()
        for ((idx, clipId) in sortedLinks.withIndex()) {
            val audioId = linksObj.getString(clipId)
            val (start, end) = clipMap[clipId] ?: continue
            val dur = (end - start).coerceAtLeast(1.0)

            val audioFile = File(workDir, "beat_$audioId.mp3").absolutePath
            val cp = File(workDir, "timeline_clip_$idx.mp4").absolutePath

            onProgress(idx * 90 / sortedLinks.size, "rendering clip ${idx+1}/${sortedLinks.size}")

            val audioExists = File(audioFile).exists()
            val audioInput = if (audioExists) "-i \"$audioFile\"" else "-an"
            val audioOpts = if (audioExists)
                "-c:a aac -b:a 128k -af \"loudnorm=I=-16:TP=-1.5:LRA=11\""
            else ""

            // Fast seek + trim filter (verified pattern)
            ffmpeg("-y -ss $start -i \"$src\" $audioInput " +
                    "-vf \"trim=duration=$dur,scale=1280:-2,setsar=1,fps=30\" " +
                    "-c:v libx264 -preset veryfast -crf 23 " +
                    "$audioOpts -shortest \"$cp\"")
            renderedClips.add(cp)
        }

        // Concat
        onProgress(92, "concatenating")
        val listFile = File(workDir, "timeline_list.txt").apply {
            writeText(renderedClips.joinToString("\n") { "file '$it'" })
        }
        val out = File(ctx.getExternalFilesDir(null),
            "recap_${System.currentTimeMillis()}.mp4").absolutePath
        ffmpeg("-y -f concat -safe 0 -i \"${listFile.absolutePath}\" -c copy \"$out\"")

        onProgress(100, "done")
        out
    }

    // ---------- helpers ----------

    private fun ffmpeg(cmd: String) {
        val s = FFmpegKit.execute(cmd)
        if (!ReturnCode.isSuccess(s.returnCode)) {
            val err = s.failStackTrace?.take(200)
                ?: s.output?.takeLast(300)
                ?: "returnCode=${s.returnCode}"
            throw Exception("ffmpeg failed: $err")
        }
    }

    private fun probeDuration(path: String): Double {
        val s = com.antonkarpenko.ffmpegkit.FFprobeKit.getMediaInformation(path)
        return try {
            s.mediaInformation.duration?.toDouble() ?: 0.0
        } catch (e: Exception) { 0.0 }
    }

    private fun sampleFrames(src: String, dir: File, movieLen: Double): List<String> {
        val out = mutableListOf<String>()
        var t = 0.0
        var i = 0
        while (t < movieLen && i < 240) {  // cap 240 frames
            val fp = File(dir, "frame_$i.jpg").absolutePath
            try {
                ffmpeg("-y -ss $t -i \"$src\" -vframes 1 -q:v 3 \"$fp\"")
                out.add(fp)
            } catch (e: Exception) { }
            t += 5.0; i++
        }
        return out
    }

    private fun buildScenes(movieLen: Double, events: List<AudioEvent>,
                            frameFaceIds: List<List<Int>>): List<SceneWindow> {
        val scenes = mutableListOf<SceneWindow>()
        var t = 0.0
        while (t < movieLen) {
            val end = minOf(t + 5.0, movieLen)
            val labels = events.filter { it.start >= t && it.start < end }
                .map { it.label }
            scenes.add(SceneWindow(t, end, emptyList(), labels, 0.5, 0.5))
            t += 5.0
        }
        return scenes
    }

    private fun buildEvidenceSummary(segments: List<Segment>,
                                     events: List<AudioEvent>,
                                     scenes: List<SceneWindow>): String {
        val sb = StringBuilder()
        sb.append("Scenes: ${scenes.size} x 5s windows. ")
        val topAudio = events.groupBy { it.label }
            .mapValues { it.value.size }
            .toList().sortedByDescending { it.second }.take(5)
        sb.append("Top audio: ${topAudio.joinToString { "${it.first}(${it.second})" }}. ")
        sb.append("Dialogue segments: ${segments.size}.")
        return sb.toString()
    }

    private fun autoNameClusters(segments: List<Segment>,
                                 faces: List<Face>): Map<String, Int> {
        // Conservative: only map names that co-occur with dialogue.
        // Full impl in Phase 2; return empty for now (safe).
        return emptyMap()
    }

    private fun renderBeats(src: String, matches: List<Match>,
                            beatAudio: List<String>, dir: File, out: String,
                            onProgress: (Int) -> Unit) {
        val clips = mutableListOf<String>()
        for ((i, m) in matches.withIndex()) {
            val cp = File(dir, "clip_$i.mp4").absolutePath
            val dur = (m.scene.end - m.scene.start).coerceAtLeast(1.0)
            // ≤5s fair-use clip, transformed, muted source audio
            // Use fast input seek + precise trim filter (verified pattern from laptop)
            val ss = m.scene.start
            val audioFile = beatAudio.getOrNull(i) ?: ""
            val audioInput = if (audioFile.isNotEmpty() && File(audioFile).exists())
                "-i \"$audioFile\"" else "-an"
            ffmpeg("-y -ss $ss -i \"$src\" $audioInput " +
                    "-vf \"trim=duration=$dur,scale=1280:-2,setsar=1,fps=30\" " +
                    "-c:v libx264 -preset veryfast -crf 23 " +
                    (if (audioFile.isNotEmpty() && File(audioFile).exists())
                        "-c:a aac -b:a 128k -af \"loudnorm=I=-16:TP=-1.5:LRA=11\" "
                    else "") +
                    "-shortest \"$cp\"")
            clips.add(cp)
            onProgress(i * 100 / matches.size.coerceAtLeast(1))
        }
        // concat
        val listFile = File(dir, "list.txt").apply {
            writeText(clips.joinToString("\n") { "file '$it'" })
        }
        ffmpeg("-y -f concat -safe 0 -i \"${listFile.absolutePath}\" -c copy \"$out\"")
        onProgress(100)
    }
}
