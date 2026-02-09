//+------------------------------------------------------------------+
//|                                              GBPJPY_Analyst.mq5  |
//|                         GBPJPY AI Trade Analyst Bot               |
//|                         Sends chart screenshots + market data     |
//|                         to FastAPI server for Claude analysis      |
//+------------------------------------------------------------------+
#property copyright "GBPJPY AI Trade Analyst Bot"
#property link      ""
#property version   "2.00"
#property strict

#include <Trade\Trade.mqh>

//--- Input parameters
input string   InpServerURL       = "http://127.0.0.1:8000/analyze"; // Server URL (analyze endpoint)
input string   InpServerBase      = "http://127.0.0.1:8000";         // Server Base URL
input int      InpLondonOpenHour  = 8;      // London Open Hour (CET)
input int      InpLondonOpenMin   = 0;      // London Open Minute
input int      InpNYOpenHour      = 14;     // NY Open Hour (CET)
input int      InpNYOpenMin       = 30;     // NY Open Minute
input int      InpTimezoneOffset  = 0;      // Timezone Offset (Server - CET) in hours
input int      InpCooldownMinutes = 30;     // Cooldown after scan (minutes)
input int      InpScreenshotWidth = 1920;   // Screenshot Width
input int      InpScreenshotHeight= 1080;   // Screenshot Height
input bool     InpManualTrigger   = false;  // Manual Trigger (set true to force scan)
input double   InpRiskPercent     = 1.0;    // Risk % per trade
input int      InpMagicNumber     = 888888; // Magic Number for trades

//--- Global variables
datetime g_lastScanTime = 0;
bool     g_londonScanned = false;
bool     g_nyScanned     = false;
int      g_lastDay       = 0;
string   g_screenshotDir;
datetime g_lastPollTime  = 0;
string   g_lastTradeId   = "";  // Prevent re-executing the same trade

//--- Trade object
CTrade g_trade;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   Print(">>> GBPJPY Analyst v2.0 - Trade Execution Enabled <<<");

   //--- Set up screenshot directory
   g_screenshotDir = "GBPJPY_Analyst";

   //--- Configure trade object
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(30);  // 3 pips slippage for GBPJPY
   g_trade.SetTypeFilling(ORDER_FILLING_IOC);

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

   //--- Poll for pending trades every 10 seconds
   if(TimeCurrent() - g_lastPollTime >= 10)
   {
      g_lastPollTime = TimeCurrent();
      PollPendingTrade();
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
//| Poll server for pending trades                                     |
//+------------------------------------------------------------------+
void PollPendingTrade()
{
   string url = InpServerBase + "/pending_trade";
   char   postData[];  // empty — this is GET
   char   result[];
   string resultHeaders;
   string headers = "";

   int res = WebRequest("GET", url, headers, 5000, postData, result, resultHeaders);
   if(res != 200)
      return;  // silently ignore — no spam in logs for routine polling

   string response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

   //--- Quick check: does response contain a pending trade?
   if(StringFind(response, "\"pending\": true") < 0 &&
      StringFind(response, "\"pending\":true") < 0)
      return;

   //--- Parse trade ID first for duplicate check
   string tradeId    = JsonGetString(response, "id");

   //--- Skip if we already executed this trade
   if(tradeId == g_lastTradeId && tradeId != "")
      return;

   Print("=== Pending trade found! ===");
   Print("Trade data: ", response);
   string bias       = JsonGetString(response, "bias");
   double entryMin   = JsonGetDouble(response, "entry_min");
   double entryMax   = JsonGetDouble(response, "entry_max");
   double stopLoss   = JsonGetDouble(response, "stop_loss");
   double tp1        = JsonGetDouble(response, "tp1");
   double tp2        = JsonGetDouble(response, "tp2");
   double slPips     = JsonGetDouble(response, "sl_pips");

   if(tradeId == "" || bias == "")
   {
      Print("ERROR: Failed to parse pending trade JSON");
      return;
   }

   //--- Execute the trade
   ExecuteTrade(tradeId, bias, entryMin, entryMax, stopLoss, tp1, tp2, slPips);
}

//+------------------------------------------------------------------+
//| Execute trade with split lots (50% TP1, 50% TP2)                   |
//| Uses LIMIT orders when price is outside entry zone                 |
//| Uses MARKET orders when price is already in the zone               |
//+------------------------------------------------------------------+
void ExecuteTrade(string tradeId, string bias, double entryMin, double entryMax,
                  double stopLoss, double tp1, double tp2, double slPips)
{
   Print("Executing trade: ", bias, " ID=", tradeId,
         " entry_zone=", DoubleToString(entryMin, 3), "-", DoubleToString(entryMax, 3));

   //--- Mark this trade as processed
   g_lastTradeId = tradeId;

   //--- Calculate lot size based on risk
   double totalLots = CalculateLotSize(slPips);
   if(totalLots <= 0)
   {
      Print("ERROR: Lot size calculation failed");
      SendExecutionReport(tradeId, 0, 0, 0, 0, 0, 0, 0, 0, "failed", "Lot size calculation failed");
      return;
   }

   //--- Split into two positions: 50% TP1, 50% TP2
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   double lotsTP1 = MathFloor((totalLots * 0.5) / lotStep) * lotStep;
   double lotsTP2 = MathFloor((totalLots * 0.5) / lotStep) * lotStep;

   if(lotsTP1 < minLot) lotsTP1 = minLot;
   if(lotsTP2 < minLot) lotsTP2 = minLot;
   if(lotsTP1 > maxLot) lotsTP1 = maxLot;
   if(lotsTP2 > maxLot) lotsTP2 = maxLot;

   Print("Lot size: total=", DoubleToString(totalLots, 2),
         " TP1=", DoubleToString(lotsTP1, 2),
         " TP2=", DoubleToString(lotsTP2, 2));

   //--- Determine current price
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entryMid = NormalizeDouble((entryMin + entryMax) / 2.0, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));

   //--- Decide: market order or limit order
   bool useLimit = false;

   if(bias == "long")
   {
      //--- LONG: want to buy when price drops INTO the entry zone
      //--- If ask is above entry zone → place BUY LIMIT at entry_min (wait for pullback)
      //--- If ask is in or below zone → market buy now
      if(ask > entryMax)
      {
         useLimit = true;
         Print("Price ", DoubleToString(ask, 3), " is ABOVE entry zone — placing BUY LIMIT at ", DoubleToString(entryMin, 3));
      }
      else
      {
         Print("Price ", DoubleToString(ask, 3), " is in/below entry zone — executing MARKET BUY");
      }
   }
   else // short
   {
      //--- SHORT: want to sell when price rises INTO the entry zone
      //--- If bid is below entry zone → place SELL LIMIT at entry_max (wait for retracement)
      //--- If bid is in or above zone → market sell now
      if(bid < entryMin)
      {
         useLimit = true;
         Print("Price ", DoubleToString(bid, 3), " is BELOW entry zone — placing SELL LIMIT at ", DoubleToString(entryMax, 3));
      }
      else
      {
         Print("Price ", DoubleToString(bid, 3), " is in/above entry zone — executing MARKET SELL");
      }
   }

   bool ok1, ok2;
   ulong ticket1 = 0, ticket2 = 0;
   double actualEntry = 0;

   if(useLimit)
   {
      //--- Place LIMIT orders (pending — will fill when price reaches entry zone)
      double limitPrice;
      ENUM_ORDER_TYPE limitType;

      if(bias == "long")
      {
         limitType = ORDER_TYPE_BUY_LIMIT;
         limitPrice = entryMin;  // Buy at bottom of entry zone
      }
      else
      {
         limitType = ORDER_TYPE_SELL_LIMIT;
         limitPrice = entryMax;  // Sell at top of entry zone
      }

      limitPrice = NormalizeDouble(limitPrice, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS));

      //--- Order 1: TP1 (close 50%)
      string comment1 = "AI_" + tradeId + "_TP1";
      ok1 = g_trade.OrderOpen(_Symbol, limitType, lotsTP1, 0, limitPrice, stopLoss, tp1,
                               ORDER_TIME_GTC, 0, comment1);
      ticket1 = ok1 ? g_trade.ResultOrder() : 0;

      if(!ok1)
      {
         Print("ERROR: TP1 limit order failed: ", g_trade.ResultRetcodeDescription());
         SendExecutionReport(tradeId, 0, 0, 0, 0, 0, 0, 0, 0, "failed",
                             "TP1 limit failed: " + g_trade.ResultRetcodeDescription());
         return;
      }
      Print("TP1 LIMIT order placed: ticket=", ticket1, " at ", DoubleToString(limitPrice, 3));

      //--- Order 2: TP2 (runner 50%)
      string comment2 = "AI_" + tradeId + "_TP2";
      ok2 = g_trade.OrderOpen(_Symbol, limitType, lotsTP2, 0, limitPrice, stopLoss, tp2,
                               ORDER_TIME_GTC, 0, comment2);
      ticket2 = ok2 ? g_trade.ResultOrder() : 0;

      if(!ok2)
      {
         Print("WARNING: TP2 limit order failed: ", g_trade.ResultRetcodeDescription());
         SendExecutionReport(tradeId, (int)ticket1, 0, lotsTP1, 0,
                             limitPrice, stopLoss, tp1, 0, "executed",
                             "TP2 limit failed: " + g_trade.ResultRetcodeDescription());
         return;
      }
      Print("TP2 LIMIT order placed: ticket=", ticket2, " at ", DoubleToString(limitPrice, 3));

      Print("=== LIMIT orders placed: ", bias, " ", DoubleToString(lotsTP1 + lotsTP2, 2),
            " lots at ", DoubleToString(limitPrice, 3), " ===");
      SendExecutionReport(tradeId, (int)ticket1, (int)ticket2, lotsTP1, lotsTP2,
                          limitPrice, stopLoss, tp1, tp2, "pending", "");
   }
   else
   {
      //--- Place MARKET orders (price is already in the entry zone)
      ENUM_ORDER_TYPE orderType;
      double price;

      if(bias == "long")
      {
         orderType = ORDER_TYPE_BUY;
         price = ask;
      }
      else
      {
         orderType = ORDER_TYPE_SELL;
         price = bid;
      }

      //--- Position 1: TP1 (close 50%)
      string comment1 = "AI_" + tradeId + "_TP1";
      ok1 = g_trade.PositionOpen(_Symbol, orderType, lotsTP1, price, stopLoss, tp1, comment1);
      ticket1 = ok1 ? g_trade.ResultOrder() : 0;
      actualEntry = ok1 ? g_trade.ResultPrice() : 0;

      if(!ok1)
      {
         Print("ERROR: TP1 market order failed: ", g_trade.ResultRetcodeDescription());
         SendExecutionReport(tradeId, 0, 0, 0, 0, 0, 0, 0, 0, "failed",
                             "TP1 market failed: " + g_trade.ResultRetcodeDescription());
         return;
      }
      Print("TP1 MARKET order filled: ticket=", ticket1, " entry=", DoubleToString(actualEntry, 3));

      //--- Position 2: TP2 (runner 50%)
      if(bias == "long")
         price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      else
         price = SymbolInfoDouble(_Symbol, SYMBOL_BID);

      string comment2 = "AI_" + tradeId + "_TP2";
      ok2 = g_trade.PositionOpen(_Symbol, orderType, lotsTP2, price, stopLoss, tp2, comment2);
      ticket2 = ok2 ? g_trade.ResultOrder() : 0;
      double actualEntry2 = ok2 ? g_trade.ResultPrice() : 0;

      if(!ok2)
      {
         Print("WARNING: TP2 market order failed: ", g_trade.ResultRetcodeDescription());
         SendExecutionReport(tradeId, (int)ticket1, 0, lotsTP1, 0,
                             actualEntry, stopLoss, tp1, 0, "executed",
                             "TP2 market failed: " + g_trade.ResultRetcodeDescription());
         return;
      }
      Print("TP2 MARKET order filled: ticket=", ticket2, " entry=", DoubleToString(actualEntry2, 3));

      Print("=== MARKET orders filled: ", bias, " ", DoubleToString(lotsTP1 + lotsTP2, 2), " lots ===");
      SendExecutionReport(tradeId, (int)ticket1, (int)ticket2, lotsTP1, lotsTP2,
                          actualEntry, stopLoss, tp1, tp2, "executed", "");
   }
}

//+------------------------------------------------------------------+
//| Calculate lot size from risk % and SL pips                         |
//+------------------------------------------------------------------+
double CalculateLotSize(double slPips)
{
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskAmount = balance * InpRiskPercent / 100.0;

   //--- Get tick value: how much 1 pip move costs per 1 lot
   double tickSize  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double point     = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   if(tickSize == 0 || slPips == 0)
      return 0;

   //--- For GBPJPY: 1 pip = 0.01 (10 points), tickSize is usually 0.001
   double pipValue = (0.01 / tickSize) * tickValue;  // value of 1 pip per 1 lot

   double lots = riskAmount / (slPips * pipValue);

   //--- Normalize to lot step
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   lots = MathFloor(lots / lotStep) * lotStep;
   if(lots < minLot) lots = minLot;
   if(lots > maxLot) lots = maxLot;

   Print("Risk calc: balance=", DoubleToString(balance, 2),
         " risk$=", DoubleToString(riskAmount, 2),
         " slPips=", DoubleToString(slPips, 1),
         " pipValue=", DoubleToString(pipValue, 4),
         " lots=", DoubleToString(lots, 2));

   return lots;
}

//+------------------------------------------------------------------+
//| Send execution report back to server                               |
//+------------------------------------------------------------------+
void SendExecutionReport(string tradeId, int ticket1, int ticket2,
                         double lots1, double lots2, double entry,
                         double sl, double actualTp1, double actualTp2,
                         string status, string errorMsg)
{
   string url = InpServerBase + "/trade_executed";

   //--- Build JSON body
   string json = "{";
   json += "\"trade_id\":\"" + tradeId + "\",";
   json += "\"ticket_tp1\":" + IntegerToString(ticket1) + ",";
   json += "\"ticket_tp2\":" + IntegerToString(ticket2) + ",";
   json += "\"lots_tp1\":" + DoubleToString(lots1, 2) + ",";
   json += "\"lots_tp2\":" + DoubleToString(lots2, 2) + ",";
   json += "\"actual_entry\":" + DoubleToString(entry, 3) + ",";
   json += "\"actual_sl\":" + DoubleToString(sl, 3) + ",";
   json += "\"actual_tp1\":" + DoubleToString(actualTp1, 3) + ",";
   json += "\"actual_tp2\":" + DoubleToString(actualTp2, 3) + ",";
   json += "\"status\":\"" + status + "\",";
   json += "\"error_message\":\"" + errorMsg + "\"";
   json += "}";

   char postData[];
   StringToCharArray(json, postData, 0, WHOLE_ARRAY, CP_UTF8);
   // Strip null terminator
   ArrayResize(postData, ArraySize(postData) - 1);

   char   result[];
   string resultHeaders;
   string headers = "Content-Type: application/json\r\n";

   int res = WebRequest("POST", url, headers, 10000, postData, result, resultHeaders);
   if(res == 200)
      Print("Execution report sent to server: ", status);
   else
      Print("WARNING: Failed to send execution report (HTTP ", res, ")");
}

//+------------------------------------------------------------------+
//| Simple JSON string parser — extract string value by key            |
//+------------------------------------------------------------------+
string JsonGetString(string json, string key)
{
   string search = "\"" + key + "\"";
   int pos = StringFind(json, search);
   if(pos < 0) return "";

   // Find the colon after the key
   pos = StringFind(json, ":", pos + StringLen(search));
   if(pos < 0) return "";

   // Find opening quote
   int start = StringFind(json, "\"", pos + 1);
   if(start < 0) return "";

   // Find closing quote
   int end = StringFind(json, "\"", start + 1);
   if(end < 0) return "";

   return StringSubstr(json, start + 1, end - start - 1);
}

//+------------------------------------------------------------------+
//| Simple JSON double parser — extract numeric value by key           |
//+------------------------------------------------------------------+
double JsonGetDouble(string json, string key)
{
   string search = "\"" + key + "\"";
   int pos = StringFind(json, search);
   if(pos < 0) return 0;

   // Find the colon
   pos = StringFind(json, ":", pos + StringLen(search));
   if(pos < 0) return 0;

   // Skip whitespace
   pos++;
   while(pos < StringLen(json) && StringGetCharacter(json, pos) == ' ')
      pos++;

   // Find end of number (comma, } or whitespace)
   int end = pos;
   while(end < StringLen(json))
   {
      ushort ch = StringGetCharacter(json, end);
      if(ch == ',' || ch == '}' || ch == ' ' || ch == '\n' || ch == '\r')
         break;
      end++;
   }

   string numStr = StringSubstr(json, pos, end - pos);
   return StringToDouble(numStr);
}

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
