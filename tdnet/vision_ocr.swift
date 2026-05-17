import Darwin
import Foundation
import ImageIO
import Vision

enum VisionOcrError: Error, CustomStringConvertible {
    case imageLoadFailed(String)

    var description: String {
        switch self {
        case .imageLoadFailed(let path):
            return "Could not load image: \(path)"
        }
    }
}

func loadImage(_ path: String) throws -> CGImage {
    let url = URL(fileURLWithPath: path) as CFURL
    guard
        let source = CGImageSourceCreateWithURL(url, nil),
        let image = CGImageSourceCreateImageAtIndex(source, 0, nil)
    else {
        throw VisionOcrError.imageLoadFailed(path)
    }
    return image
}

func recognizeText(imagePath: String) throws -> [String: Any] {
    let image = try loadImage(imagePath)
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    request.recognitionLanguages = ["ja-JP", "en-US"]
    request.minimumTextHeight = 0.003

    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])

    let observations = (request.results ?? []).sorted { left, right in
        let yDelta = abs(left.boundingBox.midY - right.boundingBox.midY)
        if yDelta > 0.01 {
            return left.boundingBox.midY > right.boundingBox.midY
        }
        return left.boundingBox.minX < right.boundingBox.minX
    }

    let lines: [[String: Any]] = observations.compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else {
            return nil
        }
        let box = observation.boundingBox
        return [
            "text": candidate.string,
            "confidence": Double(candidate.confidence),
            "box": [
                "x": Double(box.origin.x),
                "y": Double(box.origin.y),
                "width": Double(box.width),
                "height": Double(box.height),
            ],
        ]
    }

    let text = lines.compactMap { line in line["text"] as? String }.joined(separator: "\n")
    return [
        "image_path": imagePath,
        "text": text,
        "lines": lines,
    ]
}

let imagePaths = Array(CommandLine.arguments.dropFirst())

do {
    let results = try imagePaths.map { path in
        try recognizeText(imagePath: path)
    }
    let data = try JSONSerialization.data(withJSONObject: results, options: [.prettyPrinted, .sortedKeys])
    FileHandle.standardOutput.write(data)
} catch {
    let message = "vision_ocr_error: \(error)\n"
    FileHandle.standardError.write(Data(message.utf8))
    exit(1)
}
