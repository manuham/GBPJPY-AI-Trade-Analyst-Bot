//+------------------------------------------------------------------+
//|                                              GBPJPY_Analyst.mq5  |
//|                         GBPJPY AI Trade Analyst Bot               |
//|                         Sends chart screenshots + market data     |
//|                         to FastAPI server for Claude analysis      |
//+------------------------------------------------------------------+
#property copyright "GBPJPY AI Trade Analyst Bot"
#property link      ""
#property version   "1.00"
#property strict

//--- Input parameters
input string   InpServerURL       = "http://127.0.0.1:8000/analyze"; // Server URL
input int      InpLondonOpenHour  = 8;      // London Open Hour (CET)
input int      InpLondonOpenMin   = 0;      // London Open Minute
input int      InpNYOpenHour      = 14;     // NY Open Hour (CET)
input int      InpNYOpenMin       = 30;     // NY Open Minute
input int      InpTimezoneOffset  = 0;      // Timezone Offset (Server - CET) in hours
input int      InpCooldownMinutes = 30;     // Cooldown after scan (minutes)
input int      InpScreenshotWidth = 1920;   // Screenshot Width
input int      InpScreenshotHeight= 1080;   // Screenshot Height
input bool     InpManualTrigger   = false;  // Manual Trigger (set true to force scan)

//--- Global variables
datetime g_lastScanTime = 0;
bool     g_londonScanned = false;
bool     g_nyScanned     = false;
int      g_lastDay       = 0;
string   g_screenshotDir;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   //--- Set up screenshot directory
   g_screenshotDir = "GBPJPY_Analyst";

   //--- Create timer for checking every 10 seconds
   EventSetTimer(10);

   //--- Save main chart layout as template for temp charts
   if(ChartSaveTemplate(0, "gbpjpy_analyst_auto"))
      Print("Main chart template saved as gbpjpy_analyst_auto.tpl");
   else
      Print("WARNING: Failed to save main chart template (error ", GetLastError(), ")");

   //--- Create manual trigger button
   CreateManualButton();

   Print("GBPJPY Analyst EA initialized.");
   Print("Server URL: ", InpServerURL);
   Print("London Open: ", IntegerToString(InpLondonOpenHour), ":",
         StringFormat("%02d", InpLondonOpenMin), " CET");
   Print("NY Open: ", IntegerToString(InpNYOpenHour), ":",
         StringFormat("%02d", InpNYOpenMin), " CET");
   Print("Timezone offset (Server - CET): ", IntegerToString(InpTimezoneOffset), " hours");
   Print("Cooldown: ", IntegerToString(InpCooldownMinutes), " minutes");

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                    |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectDelete(0, "btnManualScan");
   ObjectDelete(0, "btnManualScanLabel");
   Print("GBPJPY Analyst EA deinitialized.");
}

//+------------------------------------------------------------------+
//| Timer function                                                      |
//+------------------------------------------------------------------+
void OnTimer()
{
   //--- Ensure scan button exists (may be lost after template operations)
   if(ObjectFind(0, "btnManualScan") < 0)
      CreateManualButton();

   //--- Reset daily flags on new day
   MqlDateTime dt;
   TimeCurrent(dt);
   if(dt.day != g_lastDay)
   {
      g_londonScanned = false;
      g_nyScanned     = false;
      g_lastDay       = dt.day;
      Print("New day detected — scan flags reset.");
   }

   //--- Check if manual trigger is set (fires once then respects cooldown)
   if(InpManualTrigger)
   {
      if(IsCooldownElapsed())
      {
         Print("Manual trigger detected via input parameter.");
         RunAnalysis("Manual");
      }
      return;
   }

   //--- Convert current server time to CET
   datetime serverTime = TimeCurrent();
   int serverHour = dt.hour;
   int serverMin  = dt.min;

   //--- CET time = server time - offset
   int cetHour = serverHour - InpTimezoneOffset;
   int cetMin  = serverMin;

   //--- Normalize hour
   if(cetHour < 0)  cetHour += 24;
   if(cetHour >= 24) cetHour -= 24;

   //--- Check London open window
   if(!g_londonScanned && IsWithinWindow(cetHour, cetMin, InpLondonOpenHour, InpLondonOpenMin))
   {
      if(IsCooldownElapsed())
      {
         Print("London open window detected (CET ", cetHour, ":", StringFormat("%02d", cetMin), ")");
         if(RunAnalysis("London"))
            g_londonScanned = true;
      }
   }

   //--- Check NY open window
   if(!g_nyScanned && IsWithinWindow(cetHour, cetMin, InpNYOpenHour, InpNYOpenMin))
   {
      if(IsCooldownElapsed())
      {
         Print("NY open window detected (CET ", cetHour, ":", StringFormat("%02d", cetMin), ")");
         if(RunAnalysis("NY"))
            g_nyScanned = true;
      }
   }
}

//+------------------------------------------------------------------+
//| Chart event handler (for manual button)                            |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK && sparam == "btnManualScan")
   {
      Print("Manual scan button clicked!");
      RunAnalysis("Manual");
      //--- Reset button state
      ObjectSetInteger(0, "btnManualScan", OBJPROP_STATE, false);
   }
}

//+------------------------------------------------------------------+
//| Check if current time is within session window (±5 min)            |
//+------------------------------------------------------------------+
bool IsWithinWindow(int currentHour, int currentMin, int targetHour, int targetMin)
{
   int currentTotal = currentHour * 60 + currentMin;
   int targetTotal  = targetHour * 60 + targetMin;
   int diff = MathAbs(currentTotal - targetTotal);
   return (diff <= 5 || diff >= (24*60 - 5));
}

//+------------------------------------------------------------------+
//| Check if cooldown period has elapsed                                |
//+------------------------------------------------------------------+
bool IsCooldownElapsed()
{
   if(g_lastScanTime == 0) return true;
   return (TimeCurrent() - g_lastScanTime) >= InpCooldownMinutes * 60;
}

//+------------------------------------------------------------------+
//| Create manual trigger button on chart                              |
//+------------------------------------------------------------------+
void CreateManualButton()
{
   //--- Remove if already exists (e.g. reloading EA)
   ObjectDelete(0, "btnManualScan");
   ResetLastError();

   //--- Create button on chart subwindow 0
   bool created = ObjectCreate(0, "btnManualScan", OBJ_BUTTON, 0, 0, 0);
   if(!created)
   {
      Print("WARNING: Failed to create scan button (error ", GetLastError(), ")");
      return;
   }

   //--- Position: top-left corner, below OHLC header
   ObjectSetInteger(0, "btnManualScan", OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_XDISTANCE, 15);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_YDISTANCE, 25);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_XSIZE, 160);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_YSIZE, 35);

   //--- Appearance
   ObjectSetString(0,  "btnManualScan", OBJPROP_TEXT, " Scan GBPJPY ");
   ObjectSetString(0,  "btnManualScan", OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, "btnManualScan", OBJPROP_FONTSIZE, 10);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_COLOR, clrWhite);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_BGCOLOR, clrDodgerBlue);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_BORDER_COLOR, clrDodgerBlue);

   //--- Behavior
   ObjectSetInteger(0, "btnManualScan", OBJPROP_STATE, false);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_BACK, false);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "btnManualScan", OBJPROP_ZORDER, 100);

   //--- Force chart to redraw so button appears immediately
   ChartRedraw(0);
   Print("Scan button created on chart.");
}

//+------------------------------------------------------------------+
//| Main analysis function                                             |
//+------------------------------------------------------------------+
bool RunAnalysis(string session)
{
   Print("=== Starting ", session, " session analysis ===");

   //--- Step 1: Capture screenshots for all 3 timeframes
   string fileH1  = CaptureTimeframeScreenshot(PERIOD_H1, "H1");
   string fileM15 = CaptureTimeframeScreenshot(PERIOD_M15, "M15");
   string fileM5  = CaptureTimeframeScreenshot(PERIOD_M5, "M5");

   if(fileH1 == "" || fileM15 == "" || fileM5 == "")
   {
      Print("ERROR: Failed to capture one or more screenshots.");
      return false;
   }

   Print("All screenshots captured successfully.");

   //--- Step 2: Gather market data JSON
   string jsonData = BuildMarketDataJSON(session);
   Print("Market data JSON built (", StringLen(jsonData), " chars).");

   //--- Step 3: Send to server
   bool result = SendToServer(fileH1, fileM15, fileM5, jsonData);

   if(result)
   {
      g_lastScanTime = TimeCurrent();
      Print("=== ", session, " analysis sent successfully ===");
   }
   else
   {
      Print("ERROR: Failed to send analysis to server.");
   }

   //--- Cleanup screenshot files
   FileDelete(fileH1);
   FileDelete(fileM15);
   FileDelete(fileM5);

   //--- Restore scan button (temp chart operations may have removed it)
   CreateManualButton();

   return result;
}

//+------------------------------------------------------------------+
//| Apply chart visual properties to a given chart                     |
//+------------------------------------------------------------------+
void ApplyChartProperties(long chartId)
{
   //--- General: Candle chart, shift, autoscroll
   ChartSetInteger(chartId, CHART_MODE, CHART_CANDLES);
   ChartSetInteger(chartId, CHART_SHIFT, true);
   ChartSetInteger(chartId, CHART_AUTOSCROLL, true);

   //--- Show: ALL options off (clean chart for analysis)
   ChartSetInteger(chartId, CHART_SHOW_TICKER, false);
   ChartSetInteger(chartId, CHART_SHOW_OHLC, false);
   ChartSetInteger(chartId, CHART_SHOW_BID_LINE, false);
   ChartSetInteger(chartId, CHART_SHOW_ASK_LINE, false);
   ChartSetInteger(chartId, CHART_SHOW_LAST_LINE, false);
   ChartSetInteger(chartId, CHART_SHOW_PERIOD_SEP, false);
   ChartSetInteger(chartId, CHART_SHOW_GRID, false);
   ChartSetInteger(chartId, CHART_SHOW_VOLUMES, CHART_VOLUME_HIDE);
   ChartSetInteger(chartId, CHART_SHOW_OBJECT_DESCR, false);
   ChartSetInteger(chartId, CHART_SHOW_TRADE_LEVELS, false);
   ChartSetInteger(chartId, CHART_SHOW_TRADE_HISTORY, false);

   //--- Colors matching user's chart layout
   ChartSetInteger(chartId, CHART_COLOR_BACKGROUND, clrSilver);
   ChartSetInteger(chartId, CHART_COLOR_FOREGROUND, clrBlack);
   ChartSetInteger(chartId, CHART_COLOR_GRID, clrSilver);
   ChartSetInteger(chartId, CHART_COLOR_CHART_UP, clrDimGray);
   ChartSetInteger(chartId, CHART_COLOR_CHART_DOWN, clrBlack);
   ChartSetInteger(chartId, CHART_COLOR_CANDLE_BULL, C'0,63,210');
   ChartSetInteger(chartId, CHART_COLOR_CANDLE_BEAR, clrBlack);
   ChartSetInteger(chartId, CHART_COLOR_CHART_LINE, clrBlack);
   ChartSetInteger(chartId, CHART_COLOR_VOLUME, clrGreen);
   ChartSetInteger(chartId, CHART_COLOR_BID, clrBlack);
   ChartSetInteger(chartId, CHART_COLOR_ASK, clrBlack);
   ChartSetInteger(chartId, CHART_COLOR_LAST, clrBlack);
   ChartSetInteger(chartId, CHART_COLOR_STOP_LEVEL, clrOrangeRed);
}

//+------------------------------------------------------------------+
//| Capture screenshot for a specific timeframe                        |
//+------------------------------------------------------------------+
string CaptureTimeframeScreenshot(ENUM_TIMEFRAMES tf, string tfLabel)
{
   string filename = g_screenshotDir + "\\" + tfLabel + "_" +
                     TimeToString(TimeCurrent(), TIME_DATE) + "_" +
                     IntegerToString(GetTickCount()) + ".png";

   //--- Open a temporary chart
   long chartId = ChartOpen(_Symbol, tf);
   if(chartId <= 0)
   {
      Print("ERROR: Failed to open temporary chart for ", tfLabel, " (error ", GetLastError(), ")");
      return "";
   }

   Print("Opened temp chart ", chartId, " for ", tfLabel);

   //--- Prevent temp chart from stealing focus from main chart
   ChartSetInteger(chartId, CHART_BRING_TO_TOP, false);

   //--- Wait for chart to fully initialize
   ChartRedraw(chartId);
   Sleep(500);

   //--- Apply saved template from main chart (most reliable method)
   if(!ChartApplyTemplate(chartId, "gbpjpy_analyst_auto"))
      Print("WARNING: Template apply failed (error ", GetLastError(), "), using manual properties");

   //--- Force redraw after template, then wait for it to take effect
   ChartRedraw(chartId);
   Sleep(2000);

   //--- Apply manual properties ON TOP of template (ensures correct even if template partial)
   ApplyChartProperties(chartId);

   //--- Final redraw and wait
   ChartRedraw(chartId);
   Sleep(1000);

   //--- Verify properties took effect
   long bgColor = ChartGetInteger(chartId, CHART_COLOR_BACKGROUND);
   long gridVisible = ChartGetInteger(chartId, CHART_SHOW_GRID);
   Print(tfLabel, " chart verify: bg_color=", bgColor, " (expect ", (long)clrSilver, "), grid=", gridVisible, " (expect 0)");

   //--- Take screenshot
   bool success = ChartScreenShot(chartId, filename, InpScreenshotWidth, InpScreenshotHeight);

   //--- Always close temporary chart
   ChartClose(chartId);

   if(!success)
   {
      Print("ERROR: ChartScreenShot failed for ", tfLabel, " (error ", GetLastError(), ")");
      return "";
   }

   Print("Screenshot captured: ", tfLabel, " -> ", filename);
   return filename;
}

//+------------------------------------------------------------------+
//| Build market data JSON string                                      |
//+------------------------------------------------------------------+
string BuildMarketDataJSON(string session)
{
   string json = "{";

   //--- Symbol info
   json += "\"symbol\":\"" + _Symbol + "\",";
   json += "\"session\":\"" + session + "\",";
   json += "\"timestamp\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\",";

   //--- Bid/Ask/Spread
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double spread = (ask - bid) / (point * 10); // Spread in pips (GBPJPY = 3 digit)

   json += "\"bid\":" + DoubleToString(bid, 3) + ",";
   json += "\"ask\":" + DoubleToString(ask, 3) + ",";
   json += "\"spread_pips\":" + DoubleToString(spread, 1) + ",";

   //--- ATR values
   json += "\"atr_h1\":" + DoubleToString(GetATR(PERIOD_H1, 14), 3) + ",";
   json += "\"atr_m15\":" + DoubleToString(GetATR(PERIOD_M15, 14), 3) + ",";
   json += "\"atr_m5\":" + DoubleToString(GetATR(PERIOD_M5, 14), 3) + ",";

   //--- Daily range
   double dayHigh = iHigh(_Symbol, PERIOD_D1, 0);
   double dayLow  = iLow(_Symbol, PERIOD_D1, 0);
   double dayRange = (dayHigh - dayLow) / (point * 10);
   json += "\"daily_high\":" + DoubleToString(dayHigh, 3) + ",";
   json += "\"daily_low\":" + DoubleToString(dayLow, 3) + ",";
   json += "\"daily_range_pips\":" + DoubleToString(dayRange, 1) + ",";

   //--- Account balance
   json += "\"account_balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";

   //--- OHLC data for each timeframe
   json += "\"ohlc_h1\":" + GetOHLCArray(PERIOD_H1, 20) + ",";
   json += "\"ohlc_m15\":" + GetOHLCArray(PERIOD_M15, 20) + ",";
   json += "\"ohlc_m5\":" + GetOHLCArray(PERIOD_M5, 20);

   json += "}";
   return json;
}

//+------------------------------------------------------------------+
//| Get ATR value for a timeframe                                      |
//+------------------------------------------------------------------+
double GetATR(ENUM_TIMEFRAMES tf, int period)
{
   int handle = iATR(_Symbol, tf, period);
   if(handle == INVALID_HANDLE)
   {
      Print("ERROR: Failed to create ATR indicator for ", EnumToString(tf));
      return 0;
   }

   double atrBuffer[];
   ArraySetAsSeries(atrBuffer, true);

   if(CopyBuffer(handle, 0, 0, 1, atrBuffer) <= 0)
   {
      Print("ERROR: Failed to copy ATR buffer for ", EnumToString(tf));
      IndicatorRelease(handle);
      return 0;
   }

   double val = atrBuffer[0];
   IndicatorRelease(handle);
   return val;
}

//+------------------------------------------------------------------+
//| Get OHLC data as JSON array                                        |
//+------------------------------------------------------------------+
string GetOHLCArray(ENUM_TIMEFRAMES tf, int count)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);

   int copied = CopyRates(_Symbol, tf, 0, count, rates);
   if(copied <= 0)
   {
      Print("ERROR: Failed to copy rates for ", EnumToString(tf));
      return "[]";
   }

   string json = "[";
   for(int i = 0; i < copied; i++)
   {
      if(i > 0) json += ",";
      json += "{";
      json += "\"time\":\"" + TimeToString(rates[i].time, TIME_DATE|TIME_MINUTES) + "\",";
      json += "\"open\":" + DoubleToString(rates[i].open, 3) + ",";
      json += "\"high\":" + DoubleToString(rates[i].high, 3) + ",";
      json += "\"low\":" + DoubleToString(rates[i].low, 3) + ",";
      json += "\"close\":" + DoubleToString(rates[i].close, 3) + ",";
      json += "\"volume\":" + IntegerToString(rates[i].tick_volume);
      json += "}";
   }
   json += "]";

   return json;
}

//+------------------------------------------------------------------+
//| Helper: append a string to a char array (no null terminator)       |
//+------------------------------------------------------------------+
void AppendStringToBody(char &body[], string text)
{
   uchar tmp[];
   StringToCharArray(text, tmp, 0, WHOLE_ARRAY, CP_UTF8);
   int len = ArraySize(tmp) - 1; // strip null terminator
   if(len <= 0) return;

   int pos = ArraySize(body);
   ArrayResize(body, pos + len);
   for(int i = 0; i < len; i++)
      body[pos + i] = (char)tmp[i];
}

//+------------------------------------------------------------------+
//| Helper: append binary bytes to a char array                        |
//+------------------------------------------------------------------+
void AppendBinaryToBody(char &body[], uchar &bin[], int binSize)
{
   int pos = ArraySize(body);
   ArrayResize(body, pos + binSize);
   for(int i = 0; i < binSize; i++)
      body[pos + i] = (char)bin[i];
}

//+------------------------------------------------------------------+
//| Send data to Python server via multipart POST                      |
//+------------------------------------------------------------------+
bool SendToServer(string fileH1, string fileM15, string fileM5, string &jsonData)
{
   string boundary = "----GBPJPYBound" + IntegerToString(GetTickCount());

   //--- Build multipart body using char[] (what WebRequest expects)
   char postData[];

   //--- Part 1: market_data (text form field — no Content-Type header)
   AppendStringToBody(postData, "--" + boundary + "\r\n");
   AppendStringToBody(postData, "Content-Disposition: form-data; name=\"market_data\"\r\n");
   AppendStringToBody(postData, "\r\n");
   AppendStringToBody(postData, jsonData);

   //--- Part 2-4: screenshot files
   AppendFilePart(postData, boundary, "screenshot_h1", fileH1);
   AppendFilePart(postData, boundary, "screenshot_m15", fileM15);
   AppendFilePart(postData, boundary, "screenshot_m5", fileM5);

   //--- Closing boundary
   AppendStringToBody(postData, "\r\n--" + boundary + "--\r\n");

   //--- Prepare headers
   string headers = "Content-Type: multipart/form-data; boundary=" + boundary + "\r\n";

   //--- Send the request
   char   result[];
   string resultHeaders;
   int timeout = 60000; // 60 second timeout

   Print("Sending to ", InpServerURL, " (", ArraySize(postData), " bytes)...");

   int res = WebRequest("POST", InpServerURL, headers, timeout, postData, result, resultHeaders);

   if(res == -1)
   {
      int error = GetLastError();
      Print("ERROR: WebRequest failed (error code ", error, ")");
      if(error == 4014)
         Print("ERROR: URL not in allowed list. Add '", InpServerURL, "' to Tools > Options > Expert Advisors > Allowed URLs");
      else if(error == 4060)
         Print("ERROR: No connection to server. Is the server running at ", InpServerURL, "?");
      else
         Print("ERROR: WebRequest error ", error, ". Check URL and network.");
      return false;
   }

   string response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   Print("Server response (HTTP ", res, "): ", response);

   if(res != 200)
   {
      Print("ERROR: Server returned HTTP ", res, " (expected 200).");
      Print("Response headers: ", resultHeaders);
   }

   return (res == 200);
}

//+------------------------------------------------------------------+
//| Append a file part to the multipart body                           |
//+------------------------------------------------------------------+
void AppendFilePart(char &body[], string boundary, string fieldName, string filepath)
{
   //--- Read the file
   int fileHandle = FileOpen(filepath, FILE_READ|FILE_BIN);
   if(fileHandle == INVALID_HANDLE)
   {
      Print("ERROR: Cannot open file ", filepath, " for reading (error ", GetLastError(), ")");
      return;
   }

   int fileSize = (int)FileSize(fileHandle);
   uchar fileData[];
   ArrayResize(fileData, fileSize);
   FileReadArray(fileHandle, fileData, 0, fileSize);
   FileClose(fileHandle);

   Print("Read file ", filepath, " (", fileSize, " bytes)");

   //--- Extract just the filename from the path
   string shortName = filepath;
   int slashPos = StringFind(filepath, "\\");
   while(slashPos >= 0)
   {
      shortName = StringSubstr(filepath, slashPos + 1);
      slashPos = StringFind(filepath, "\\", slashPos + 1);
   }

   //--- Boundary separator + part headers
   AppendStringToBody(body, "\r\n--" + boundary + "\r\n");
   AppendStringToBody(body, "Content-Disposition: form-data; name=\"" + fieldName + "\"; filename=\"" + shortName + "\"\r\n");
   AppendStringToBody(body, "Content-Type: image/png\r\n");
   AppendStringToBody(body, "\r\n");

   //--- Binary file content
   AppendBinaryToBody(body, fileData, fileSize);
}
//+------------------------------------------------------------------+
