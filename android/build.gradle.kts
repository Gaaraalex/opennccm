// App Android de OpenNCCM (Kotlin + Jetpack Compose). Se compila en la CI (.github/workflows/ci.yml, trabajo «android»)
// con «gradle assembleRelease» y se publica junto a cada versión; el servidor la entrega desde Mi cuenta › App Android.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.21" apply false
}
