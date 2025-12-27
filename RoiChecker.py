import cv2
import pyautogui

# Load your image
image_path = "IMG_1612.png"
image = cv2.imread(image_path)

if image is None:
    print("Error: Could not load image. Make sure the path is correct.")
    exit()

# Get screen resolution
screen_width, screen_height = pyautogui.size()

# Create a named window
cv2.namedWindow("Select ROI", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Select ROI", 2560, 1600)  # Resize window if needed

# Move window so the bottom aligns with screen bottom
window_width = 800
window_height = 600
x_pos = (screen_width - window_width) // 2  # center horizontally
y_pos = screen_height - window_height        # bottom aligns
cv2.moveWindow("Select ROI", x_pos, y_pos)

# Let user select ROI
roi = cv2.selectROI("Select ROI", image, fromCenter=False, showCrosshair=True)
cv2.destroyAllWindows()

# roi returns (x, y, w, h)
x, y, w, h = roi

print(f"Selected Region:")
print(f"Position -> x: {x}, y: {y}")
print(f"Size -> width: {w}, height: {h}")

# Optional: show the selected area
selected_area = image[y:y+h, x:x+w]
cv2.imshow("Selected Area", selected_area)
cv2.waitKey(0)
cv2.destroyAllWindows()
