
#include <opencv2/imgcodecs/imgcodecs.hpp>
#include <opencv2/imgproc/imgproc.hpp>
#include <string>

#include "mmdeploy/text_detector.hpp"
#include "mmdeploy/text_recognizer.hpp"

int main(int argc, char* argv[]) {
  if (argc != 5) {
    fprintf(stderr, "usage:\n  ocr device_name det_model_path reg_model_path image_path\n");
    return 1;
  }
  const auto device_name = argv[1];
  auto det_model_path = argv[2];
  auto reg_model_path = argv[3];
  auto image_path = argv[4];
  cv::Mat img = cv::imread(image_path);
  if (!img.data) {
    fprintf(stderr, "failed to load image: %s\n", image_path);
    return 1;
  }

  using namespace mmdeploy;

  Context context(Device(device_name, 0));
  mmdeploy::Profiler profiler{"/deploee-tmp/profile.bin"};
  context.Add(profiler);

  TextDetector detector{Model(det_model_path), context};
  TextRecognizer recognizer{Model(reg_model_path), context};

  for (int i = 0; i < 20; ++i) {
    auto bboxes = detector.Apply(img);
    recognizer.Apply(img, {bboxes.begin(), bboxes.size()});
  }

  auto bboxes = detector.Apply(img);
  auto texts = recognizer.Apply(img, {bboxes.begin(), bboxes.size()});

  for (int i = 0; i < bboxes.size(); ++i) {
    fprintf(stdout, "box[%d]: %s\n", i, texts[i].text);
    std::vector<cv::Point> poly_points;
    for (const auto& pt : bboxes[i].bbox) {
      fprintf(stdout, "x: %.2f, y: %.2f, ", pt.x, pt.y);
      poly_points.emplace_back((int)pt.x, (int)pt.y);
    }
    fprintf(stdout, "\n");
    cv::polylines(img, poly_points, true, cv::Scalar{0, 255, 0});
  }

  cv::imwrite("output_ocr.png", img);

  return 0;
}
