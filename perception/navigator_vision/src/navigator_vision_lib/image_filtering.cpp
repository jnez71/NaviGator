#include <navigator_vision_lib/image_filtering.hpp>

cv::Mat makeRotInvariant(const cv::Mat &kernel, int rotations)
{
  // Determine affine transform to move from top left to center of output size
  cv::Point2f c_org{kernel.cols * 0.5f, kernel.rows * 0.5f};  // center of original
  float hypot = std::hypot(c_org.x, c_org.y);
  cv::Point2f c_dest{hypot, hypot};
  float center_dest_coeffs[6] = {1.0f, 0.0f, c_dest.x - c_org.x, 0.0f, 1.0f, c_dest.y - c_org.y};
  cv::Mat center_dest = cv::Mat{2, 3, CV_32F, center_dest_coeffs};

  // Move into rotation position in larger canvas
  cv::Mat dest = cv::Mat::zeros(cv::Size(hypot * 2, hypot * 2), CV_8U);
  auto dest_top_left = dest(cv::Rect(0, 0, kernel.cols, kernel.rows));
  kernel.copyTo(dest_top_left);
  cv::warpAffine(dest, dest, center_dest, dest.size());  // center dest is ready for rotating

  // Get angles of rotation
  std::vector<float> rotation_angles;
  float delta_theta = 360.0f / rotations;
  float theta = 0.0f;
  while(true)
  {
    theta += delta_theta;
    if(theta < 360.0f)
      rotation_angles.push_back(theta);
    else
      break;
  }

  // Incrementally rotate and save versions of original kernel
  std::vector<cv::Mat> kernel_rotations;
  kernel_rotations.push_back(dest);
  int i = 0;
  for(auto theta : rotation_angles)
  {
    cv::Mat rotated_kernel;
    cv::Mat rot_mat = cv::getRotationMatrix2D(c_dest, theta, 1.0);
    cv::warpAffine(dest, rotated_kernel, rot_mat, dest.size());
    kernel_rotations.push_back(rotated_kernel);
  }

  // Average all rotated versions
  cv::Mat sum = cv::Mat::zeros(dest.size(), CV_32S);
  for(auto& rot_kernel : kernel_rotations)
    cv::add(sum, rot_kernel, sum, cv::Mat(), CV_32S);
  cv::Mat result = sum / float(kernel_rotations.size());
  result.convertTo(result, kernel.type());
  return result;
}