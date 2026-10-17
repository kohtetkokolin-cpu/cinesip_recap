package com.cinesip.recap

import android.annotation.SuppressLint
import android.app.Activity
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.*
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    private lateinit var web: WebView
    private var pickedPath: String? = null

    private val pickVideo = registerForActivityResult(
        ActivityResultContracts.GetContent()) { uri: Uri? ->
        uri?.let {
            pickedPath = FileUtil.getPath(this, it) ?: it.toString()
            web.evaluateJavascript("onVideoPicked('$pickedPath')", null)
        }
    }

    private val pickAudio = registerForActivityResult(
        ActivityResultContracts.GetContent()) { uri: Uri? ->
        uri?.let {
            val path = FileUtil.getPath(this, it) ?: it.toString()
            web.evaluateJavascript("onAudioPicked('$path')", null)
        }
    }

    private val pickLogo = registerForActivityResult(
        ActivityResultContracts.GetContent()) { uri: Uri? ->
        uri?.let {
            val path = FileUtil.getPath(this, it) ?: it.toString()
            web.evaluateJavascript("onLogoPicked('$path')", null)
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        setContentView(web)
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = true
        }
        web.webChromeClient = WebChromeClient()
        web.addJavascriptInterface(CinesipBridge(this), "CinesipBridge")
        web.loadUrl("file:///android_asset/wizard.html")
    }

    fun openPicker() = pickVideo.launch("video/*")
    fun openAudioPicker() = pickAudio.launch("audio/*")
    fun openLogoPicker() = pickLogo.launch("image/*")
    fun getPicked(): String? = pickedPath
    fun webEval(js: String) { web.evaluateJavascript(js, null) }
    fun scope() = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.Main)
    fun playAudio(path: String) {
        try {
            val mp = android.media.MediaPlayer()
            mp.setDataSource(path)
            mp.prepare()
            mp.start()
            mp.setOnCompletionListener { it.release() }
        } catch (e: Exception) { toast("❌ " + (e.message ?: "error")) }
    }
    fun prefs(key: String): String? =
        getSharedPreferences("cinesip", MODE_PRIVATE).getString(key, null)
    fun prefs(key: String, value: String) =
        getSharedPreferences("cinesip", MODE_PRIVATE).edit().putString(key, value).apply()
    fun toast(m: String) =
        runOnUiThread { Toast.makeText(this, m, Toast.LENGTH_SHORT).show() }
    fun onProgress(p: Int, s: String) =
        runOnUiThread { web.evaluateJavascript("onProgress($p,'$s')", null) }
    fun onRenderDone(path: String) =
        runOnUiThread { web.evaluateJavascript("onRenderDone('$path')", null) }

    override fun onBackPressed() {
        if (web.canGoBack()) web.goBack() else super.onBackPressed()
    }
}
