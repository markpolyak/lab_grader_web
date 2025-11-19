# Course Logos

This directory contains logo images for courses displayed in the web interface.

## File Naming Convention

Use descriptive names that match course IDs:

```
os-2025-spring.png
ml-2025-spring.svg
programming-2024-fall.jpg
```

## Supported Formats

- PNG (recommended for photos/complex images)
- SVG (recommended for vector graphics/icons)
- JPG/JPEG (for photos)
- WebP (for modern browsers)

## Recommended Dimensions

- **Minimum**: 200x200 pixels
- **Recommended**: 400x400 pixels
- **Maximum**: 1000x1000 pixels

Keep file sizes small (< 200KB) for fast loading.

## Usage

Logo paths are configured in `courses/index.yaml`:

```yaml
courses:
  - id: "os-2025-spring"
    file: "operating-systems-2025.yaml"
    logo: "courses/logos/os-2025-spring.png"  # Path from project root
    status: "active"
    priority: 100
```

If no logo is specified, a default placeholder will be used.

## Adding a New Logo

1. **Add image file** to this directory:
   ```bash
   cp my-logo.png courses/logos/os-2025-spring.png
   ```

2. **Update index.yaml**:
   ```yaml
   - id: "os-2025-spring"
     file: "operating-systems-2025.yaml"
     logo: "courses/logos/os-2025-spring.png"
   ```

3. **Commit to git**:
   ```bash
   git add courses/logos/os-2025-spring.png courses/index.yaml
   git commit -m "Add logo for OS course"
   ```

## Removing a Logo

Simply remove the `logo` field from index.yaml. The system will use a default placeholder.

You can optionally delete the image file if it's no longer needed.

## Default Placeholder

If no logo is specified, the system uses: `/assets/default.png`

This default can be customized in the backend code or frontend assets.
