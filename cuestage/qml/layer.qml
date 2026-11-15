// Un layer dell'uscita: un riquadro (la parte dopo il crop) che contiene il video o l'immagine intera spostata
// del crop; la matrice porta il riquadro nel quadrilatero d'uscita (posizione, scala, rotazione, prospettiva,
// correzione a 4 angoli). Il fotogramma del video arriva dal decoder senza passare dal processore.
import QtQuick
import QtMultimedia

Item {
    id: lay
    property matrix4x4 m
    property bool isVideo: true
    property real srcX: 0
    property real srcY: 0
    property real fullW: 1
    property real fullH: 1
    clip: true
    transform: Matrix4x4 { matrix: lay.m }

    VideoOutput {
        objectName: "vo"
        visible: lay.isVideo
        x: -lay.srcX; y: -lay.srcY
        width: lay.fullW; height: lay.fullH
        fillMode: VideoOutput.Stretch
    }
    Image {
        objectName: "img"
        visible: !lay.isVideo
        x: -lay.srcX; y: -lay.srcY
        width: lay.fullW; height: lay.fullH
        fillMode: Image.Stretch
        cache: false
        smooth: true
        asynchronous: false
    }
}
