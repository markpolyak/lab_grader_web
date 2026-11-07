"""Build an atomic Google Sheets request for one readable lesson block."""


def block_requests(sheet_id, start, rows, *, row_count, col_count):
    requests = []
    end = start + len(rows)
    requests.append({"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {
            "rowCount": max(row_count, end), "columnCount": max(col_count, 5),
            "hideGridlines": False,
        }}, "fields": "gridProperties(rowCount,columnCount,hideGridlines)",
    }})
    cells = []
    for index, row in enumerate(rows):
        values = []
        for value in row:
            cell = {"userEnteredValue": {
                "numberValue" if isinstance(value, int) else "stringValue": value,
            }}
            style = {"verticalAlignment": "MIDDLE", "wrapStrategy": "WRAP",
                     "textFormat": {"fontFamily": "Arial", "fontSize": 10}}
            if index == 0:
                style["textFormat"].update(bold=True, fontSize=12)
            elif index == 3:
                style["textFormat"]["bold"] = True
            cell["userEnteredFormat"] = style
            values.append(cell)
        cells.append({"values": values})
    requests.append({"updateCells": {
        "start": {"sheetId": sheet_id, "rowIndex": start, "columnIndex": 0},
        "rows": cells, "fields": "userEnteredValue,userEnteredFormat",
    }})
    for offset in range(3):
        requests.append({"mergeCells": {"range": {
            "sheetId": sheet_id, "startRowIndex": start + offset,
            "endRowIndex": start + offset + 1, "startColumnIndex": 0, "endColumnIndex": 5,
        }, "mergeType": "MERGE_ALL"}})
    border = {"style": "SOLID", "color": {"red": .55, "green": .55, "blue": .55}}
    requests.append({"updateBorders": {
        "range": {"sheetId": sheet_id, "startRowIndex": start + 3,
                  "endRowIndex": end, "startColumnIndex": 0, "endColumnIndex": 5},
        **{edge: border for edge in ("top", "bottom", "left", "right", "innerHorizontal", "innerVertical")},
    }})
    for column, width in enumerate((50, 310, 190, 230, 180)):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": column, "endIndex": column + 1},
            "properties": {"pixelSize": width}, "fields": "pixelSize",
        }})
    requests.append({"updateDimensionProperties": {
        "range": {"sheetId": sheet_id, "dimension": "ROWS", "startIndex": start, "endIndex": end},
        "properties": {"pixelSize": 32}, "fields": "pixelSize",
    }})
    return requests
