//+------------------------------------------------------------------+
//|                                                  AI_Analyst.mq5  |
//|                   AI Trade Analyst Bot v6.0                        |
//|                   Smart Entry: M1 Confirmation + London Kill Zone  |
//|                   Break-even after TP1, trailing stop to TP1       |
//|                   Leader/follower mode for multi-account           |
//+------------------------------------------------------------------+
#property copyright "AI Trade Analyst Bot"
#property link      ""
#property version   "6.00"
#property strict

#include <Trade\Trade.mqh>

//--- Input parameters
input string   InpServerURL       = "http://46.225.66.110:8000/analyze"; // Server URL (analyze endpoint)
input string   InpServerBase      = "http://46.225.66.110:8000";         // Server Base URL
input int      InpKillZoneStart   = 8;      // Kill Zone Start Hour (MEZ)
input int      InpKillZoneStartMin= 0;      // Kill Zone Start Minute
input int      InpKillZoneEnd     = 20;     // Kill Zone End Hour (MEZ) — watches expire here
input int      InpTimezoneOffset  = 1;      // Timezone Offset (Server - MEZ) in hours
input int      InpCooldownMinutes = 30;     // Cooldown after scan (minutes)
input int      InpScreenshotWidth = 2560;   // Screenshot Width
input int      InpScreenshotHeight= 1440;   // Screenshot Height
input bool     InpManualTrigger   = false;  // Manual Trigger (set true to force scan)
input double   InpRiskPercent     = 1.0;    // Risk % per trade
input int      InpMagicNumber     = 888888; // Magic Number for trades
input int      InpConfirmCooldown = 300;     // Seconds between M1 confirmation attempts
input string   InpMode            = "leader";  // Mode: "leader" (analyze+trade) or "follower" (trade only)
input string   InpSymbolOverride  = "";        // Symbol override for server (e.g. "GBPJPY" when broker uses "GBPJPYm")
input string   InpApiKey          = "";        // API Key for server authentication (must match server .env API_KEY)

//--- Global variables
datetime g_lastScanTime = 0;
bool     g_killZoneScanned = false;
int      g_lastDay       = 0;
string   g_screenshotDir;
string   g_templateName;
int      g_digits;         // Price digits for this symbol (e.g. 3 for JPY, 5 for EUR/USD, 2 for gold)
double   g_pipSize;        // Size of 1 pip in price units (e.g. 0.01 for JPY, 0.0001 for EUR/USD)
datetime g_lastPollTime  = 0;
datetime g_lastWatchPoll = 0;
string   g_lastTradeId   = "";  // Prevent re-executing the same trade

//--- Leader/Follower mode
bool     g_isLeader     = true;   // true = leader (analyze + trade), false = follower (trade only)
string   g_serverSymbol = "";     // Symbol name used for server communication (may differ from _Symbol)

//--- Zone watching (replaces limit orders — smart entry via M1 confirmation)
bool     g_hasWatch      = false;
string   g_watchTradeId  = "";
string   g_watchBias     = "";
double   g_watchZoneMin  = 0;
double   g_watchZoneMax  = 0;
double   g_watchSL       = 0;
double   g_watchTP1      = 0;
double   g_watchTP2      = 0;
double   g_watchSlPips   = 0;
int      g_watchMaxChecks = 3;
datetime g_lastConfirmTime = 0;  // Cooldown between confirmation attempts

//--- Open position tracking (for close detection)
ulong    g_posTicket1    = 0;   // TP1 position ticket (market orders)
ulong    g_posTicket2    = 0;   // TP2 position ticket (market orders)
string   g_posTradeId    = "";  // Trade ID for the currently tracked positions

//--- Trade level tracking (for break-even and trailing stop management)
double   g_tradeEntry    = 0;   // Actual entry price
double   g_tradeSL       = 0;   // Original SL price
double   g_tradeTP1      = 0;   // TP1 level
double   g_tradeTP2      = 0;   // TP2 level
string   g_tradeBias     = "";  // "long" or "short"
bool     g_tp1Hit        = false; // Whether TP1 has been hit (for break-even tracking)
double   g_tp1ClosePct   = 50.0; // % of position to close at TP1 (from server)

//--- Visual chart enhancements
string   g_watchConfidence     = "";    // "HIGH", "MEDIUM", "LOW" from server
string   g_watchChecklistScore = "";    // e.g. "10/12" from server
double   g_watchRRatio         = 0;     // Risk:Reward ratio (calculated)

//--- Trade object
CTrade g_trade;

//--- Indicator handles (created once in OnInit, released in OnDeinit)
int g_hATR_D1  = INVALID_HANDLE;
int g_hATR_H4  = INVALID_HANDLE;
int g_hATR_H1  = INVALID_HANDLE;
int g_hATR_M5  = INVALID_HANDLE;
int g_hRSI_D1  = INVALID_HANDLE;
int g_hRSI_H4  = INVALID_HANDLE;
int g_hRSI_H1  = INVALID_HANDLE;
int g_hRSI_M5  = INVALID_HANDLE;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
{
   Print(">>> AI Analyst v6.0 [", _Symbol, "] - Smart Entry + London Kill Zone <<<");

   //--- Determine mode
   g_isLeader = (InpMode != "follower");

   //--- Server symbol: use override if set, otherwise _Symbol
   g_serverSymbol = (InpSymbolOverride != "") ? InpSymbolOverride : _Symbol;

   //--- Set up symbol-specific globals
   g_digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   g_screenshotDir = "AI_Analyst_" + _Symbol;
   g_templateName = "ai_analyst_" + _Symbol;

   //--- Determine pip size based on broker digit count
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(g_digits == 3 || g_digits == 5)
      g_pipSize = point * 10;   // fractional pip pricing (most modern brokers)
   else
      g_pipSize = point;        // 2-digit (gold) or 4-digit pairs

   //--- Configure trade object
   g_trade.SetExpertMagicNumber(InpMagicNumber);
   g_trade.SetDeviationInPoints(30);
   g_trade.SetTypeFilling(ORDER_FILLING_IOC);

   //--- Create indicator handles ONCE (avoid handle leak on every timer tick)
   g_hATR_D1 = iATR(_Symbol, PERIOD_D1, 14);
   g_hATR_H4 = iATR(_Symbol, PERIOD_H4, 14);
   g_hATR_H1 = iATR(_Symbol, PERIOD_H1, 14);
   g_hATR_M5 = iATR(_Symbol, PERIOD_M5, 14);
   g_hRSI_D1 = iRSI(_Symbol, PERIOD_D1, 14, PRICE_CLOSE);
   g_hRSI_H4 = iRSI(_Symbol, PERIOD_H4, 14, PRICE_CLOSE);
   g_hRSI_H1 = iRSI(_Symbol, PERIOD_H1, 14, PRICE_CLOSE);
   g_hRSI_M5 = iRSI(_Symbol, PERIOD_M5, 14, PRICE_CLOSE);

   if(g_hATR_D1 == INVALID_HANDLE || g_hATR_H4 == INVALID_HANDLE ||
      g_hATR_H1 == INVALID_HANDLE || g_hATR_M5 == INVALID_HANDLE ||
      g_hRSI_D1 == INVALID_HANDLE || g_hRSI_H4 == INVALID_HANDLE ||
      g_hRSI_H1 == INVALID_HANDLE || g_hRSI_M5 == INVALID_HANDLE)
   {
      Print("WARNING: Some indicator handles failed to create (data may not be available yet)");
   }

   //--- Create timer for checking every 10 seconds
   EventSetTimer(10);

   //--- Leader-only: save chart template for screenshots
   if(g_isLeader)
   {
      if(ChartSaveTemplate(0, g_templateName))
         Print("Main chart template saved as ", g_templateName, ".tpl");
      else
         Print("WARNING: Failed to save main chart template (error ", GetLastError(), ")");
   }

   //--- Create button (scan for leader, status for follower)
   CreateManualButton();

   Print(_Symbol, " AI Analyst EA initialized.");
   Print("MODE: ", (g_isLeader ? "LEADER (analyze + trade)" : "FOLLOWER (trade only)"));
   if(g_serverSymbol != _Symbol)
      Print("Symbol mapping: broker=", _Symbol, " → server=", g_serverSymbol);
   Print("Symbol digits: ", g_digits, " | Pip size: ", DoubleToString(g_pipSize, g_digits));
   Print("Server Base: ", InpServerBase);

   if(g_isLeader)
   {
      Print("Server URL: ", InpServerURL);
      Print("Kill Zone: ", IntegerToString(InpKillZoneStart), ":",
            StringFormat("%02d", InpKillZoneStartMin), " - ",
            IntegerToString(InpKillZoneEnd), ":00 MEZ");
      Print("Timezone offset (Server - MEZ): ", IntegerToString(InpTimezoneOffset), " hours");
      Print("Cooldown: ", IntegerToString(InpCooldownMinutes), " minutes");
      Print("M1 confirm cooldown: ", IntegerToString(InpConfirmCooldown), " seconds");
   }

   Print("API Key: ", (InpApiKey != "" ? "configured" : "NOT SET (no authentication)"));

   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Build auth header string for API requests                         |
//+------------------------------------------------------------------+
string GetAuthHeader()
{
   if(InpApiKey == "") return "";
   return "X-API-Key: " + InpApiKey + "\r\n";
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                    |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectDelete(0, "btnManualScan");
   ObjectDelete(0, "btnManualScanLabel");
   DeleteAllVisuals();

   //--- Release indicator handles
   if(g_hATR_D1 != INVALID_HANDLE) IndicatorRelease(g_hATR_D1);
   if(g_hATR_H4 != INVALID_HANDLE) IndicatorRelease(g_hATR_H4);
   if(g_hATR_H1 != INVALID_HANDLE) IndicatorRelease(g_hATR_H1);
   if(g_hATR_M5 != INVALID_HANDLE) IndicatorRelease(g_hATR_M5);
   if(g_hRSI_D1 != INVALID_HANDLE) IndicatorRelease(g_hRSI_D1);
   if(g_hRSI_H4 != INVALID_HANDLE) IndicatorRelease(g_hRSI_H4);
   if(g_hRSI_H1 != INVALID_HANDLE) IndicatorRelease(g_hRSI_H1);
   if(g_hRSI_M5 != INVALID_HANDLE) IndicatorRelease(g_hRSI_M5);

   Print(_Symbol, " AI Analyst EA deinitialized.");
}

//+------------------------------------------------------------------+
//| Timer function                                                      |
//+------------------------------------------------------------------+
void OnTimer()
{
   //--- Ensure button exists (may be lost after template operations)
   if(ObjectFind(0, "btnManualScan") < 0)
      CreateManualButton();

   //--- Reset daily flags on new day
   MqlDateTime dt;
   TimeCurrent(dt);
   if(dt.day != g_lastDay)
   {
      g_killZoneScanned = false;
      g_hasWatch        = false;
      g_lastDay         = dt.day;
      DeleteAllVisuals();
      Print("New day detected — scan flags reset.");
   }

   //--- Convert current server time to MEZ
   int serverHour = dt.hour;
   int serverMin  = dt.min;
   int mezHour = serverHour - InpTimezoneOffset;
   int mezMin  = serverMin;
   if(mezHour < 0)  mezHour += 24;
   if(mezHour >= 24) mezHour -= 24;

   //--- LEADER-ONLY: Kill Zone scanning
   if(g_isLeader)
   {
      //--- Check if manual trigger is set (fires once then respects cooldown)
      if(InpManualTrigger)
      {
         if(IsCooldownElapsed())
         {
            Print("Manual trigger detected via input parameter.");
            RunAnalysis("Manual");
         }
      }
      else
      {
         //--- Check Kill Zone start window
         if(!g_killZoneScanned && IsWithinWindow(mezHour, mezMin, InpKillZoneStart, InpKillZoneStartMin))
         {
            if(IsCooldownElapsed())
            {
               Print("Kill Zone start detected (MEZ ", mezHour, ":", StringFormat("%02d", mezMin), ")");
               if(RunAnalysis("London"))
                  g_killZoneScanned = true;
            }
         }
      }
   }

   //--- ALL MODES: Poll for watch trades every 10 seconds (zone monitoring)
   if(TimeCurrent() - g_lastWatchPoll >= 10)
   {
      g_lastWatchPoll = TimeCurrent();
      PollWatchTrade();
   }

   //--- ALL MODES: Check if price reached the watched entry zone
   if(g_hasWatch)
      CheckZoneReached(mezHour);

   //--- ALL MODES: Update chart visuals (entry zone, info panel)
   UpdateVisuals();

   //--- ALL MODES: Poll for pending trades (confirmed by Haiku or manual Execute)
   if(TimeCurrent() - g_lastPollTime >= 10)
   {
      g_lastPollTime = TimeCurrent();
      PollPendingTrade();
   }

   //--- ALL MODES: Check if tracked positions have closed (TP/SL hit)
   CheckPositionClosures();

   //--- ALL MODES: Manage trailing stop on TP2 runner
   ManageTrailingStop();
}

//+------------------------------------------------------------------+
//| Poll server for watch trades (zone to monitor)                     |
//+------------------------------------------------------------------+
void PollWatchTrade()
{
   string url = InpServerBase + "/watch_trade?symbol=" + g_serverSymbol;
   char   postData[];
   char   result[];
   string resultHeaders;
   string headers = GetAuthHeader();

   int res = WebRequest("GET", url, headers, 5000, postData, result, resultHeaders);
   if(res != 200)
      return;

   string response = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);

   //--- Check if there's an active watch
   if(StringFind(response, "\"has_watch\": true") < 0 &&
      StringFind(response, "\"has_watch\":true") < 0)
   {
      if(g_hasWatch)
      {
         Print("Watch trade cleared by server.");
         g_hasWatch = false;
         g_watchConfidence = "";
         g_watchChecklistScore = "";
         g_watchRRatio = 0;
         DeleteAllVisuals();
      }
      return;
   }

   //--- Parse watch trade data
   string tradeId = JsonGetString(response, "id");

   //--- Skip if we already have this watch
   if(tradeId == g_watchTradeId && g_hasWatch)
      return;

   g_watchTradeId  = tradeId;
   g_watchBias     = JsonGetString(response, "bias");
   g_watchZoneMin  = JsonGetDouble(response, "entry_min");
   g_watchZoneMax  = JsonGetDouble(response, "entry_max");
   g_watchSL       = JsonGetDouble(response, "stop_loss");
   g_watchTP1      = JsonGetDouble(response, "tp1");
   g_watchTP2      = JsonGetDouble(response, "tp2");
   g_watchSlPips   = JsonGetDouble(response, "sl_pips");
   g_watchMaxChecks = (int)JsonGetDouble(response, "max_confirmations");
   if(g_watchMaxChecks <= 0) g_watchMaxChecks = 3;

   //--- Parse visual display fields
   g_watchConfidence     = JsonGetString(response, "confidence");
   g_watchChecklistScore = JsonGetString(response, "checklist_score");

   //--- Calculate R:R ratio from entry midpoint to TP2 vs SL
   if(g_watchSlPips > 0)
   {
      double entryMid = (g_watchZoneMin + g_watchZoneMax) / 2.0;
      double tp2Pips  = MathAbs(g_watchTP2 - entryMid) / g_pipSize;
      g_watchRRatio   = tp2Pips / g_watchSlPips;
   }
   else
      g_watchRRatio = 0;

   g_hasWatch      = true;
   g_lastConfirmTime = 0;

   Print("=== ", _Symbol, " WATCH started: ", g_watchBias, " zone ",
         DoubleToString(g_watchZoneMin, g_digits), "-", DoubleToString(g_watchZoneMax, g_digits),
         " (ID: ", g_watchTradeId, ") ===");
}

//+------------------------------------------------------------------+
//| Check if price reached the watched entry zone                      |
//+------------------------------------------------------------------+
void CheckZoneReached(int mezHour)
{
   if(!g_hasWatch) return;

   //--- Cancel watch if Kill Zone ended
   if(mezHour >= InpKillZoneEnd)
   {
      Print("Kill Zone ended (MEZ ", mezHour, ":00) — cancelling watch ", g_watchTradeId);
      g_hasWatch = false;
      DeleteAllVisuals();
      return;
   }

   //--- Get current price based on bias
   double price;
   if(g_watchBias == "long")
      price = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   else
      price = SymbolInfoDouble(_Symbol, SYMBOL_BID);

   //--- Check if price is within entry zone
   if(price < g_watchZoneMin || price > g_watchZoneMax)
      return;

   //--- Respect confirmation cooldown (prevent rapid-fire Haiku calls)
   if(g_lastConfirmTime > 0 && TimeCurrent() - g_lastConfirmTime < InpConfirmCooldown)
      return;

   Print("ZONE REACHED! Price ", DoubleToString(price, g_digits),
         " in zone ", DoubleToString(g_watchZoneMin, g_digits), "-",
         DoubleToString(g_watchZoneMax, g_digits));

   //--- Capture M1 screenshot for confirmation
   string fileM1 = CaptureTimeframeScreenshot(PERIOD_M1, "M1_confirm");
   if(fileM1 == "")
   {
      Print("ERROR: Failed to capture M1 screenshot for confirmation");
      return;
   }

   //--- Send to /confirm_entry endpoint
   string response = "";
   bool confirmed = SendConfirmation(fileM1, g_watchTradeId, g_watchBias, price, response);
   g_lastConfirmTime = TimeCurrent();

   FileDelete(fileM1);
   CreateManualButton();  // Restore button after temp chart

   if(confirmed)
   {
      //--- Server converts watch → pending trade
      //--- Normal PollPendingTrade() will pick it up next cycle
      g_hasWatch = false;
      Print("=== M1 CONFIRMED — trade will execute via pending_trade poll ===");
   }
   else
   {
      //--- Check if server says remaining_checks is 0 → stop watching immediately
      int remainingChecks = (int)JsonGetDouble(response, "remaining_checks");
      if(remainingChecks <= 0 && StringFind(response, "remaining_checks") >= 0)
      {
         g_hasWatch = false;
         DeleteAllVisuals();
         Print("M1 REJECTED — all confirmation attempts exhausted, watch cancelled");
      }
      else
      {
         Print("M1 REJECTED — ", remainingChecks, " attempts remaining, will retry on next zone touch");
      }
   }
}

//+------------------------------------------------------------------+
//| Send M1 confirmation request to server                             |
//+------------------------------------------------------------------+
bool SendConfirmation(string fileM1, string tradeId, string bias, double currentPrice, string &responseOut)
{
   string url = InpServerBase + "/confirm_entry";
   string boundary = "----AIConfirm" + IntegerToString(GetTickCount());

   char postData[];

   //--- Part 1: trade_id
   AppendStringToBody(postData, "--" + boundary + "\r\n");
   AppendStringToBody(postData, "Content-Disposition: form-data; name=\"trade_id\"\r\n\r\n");
   AppendStringToBody(postData, tradeId);

   //--- Part 2: symbol
   AppendStringToBody(postData, "\r\n--" + boundary + "\r\n");
   AppendStringToBody(postData, "Content-Disposition: form-data; name=\"symbol\"\r\n\r\n");
   AppendStringToBody(postData, g_serverSymbol);

   //--- Part 3: bias
   AppendStringToBody(postData, "\r\n--" + boundary + "\r\n");
   AppendStringToBody(postData, "Content-Disposition: form-data; name=\"bias\"\r\n\r\n");
   AppendStringToBody(postData, bias);

   //--- Part 4: current_price
   AppendStringToBody(postData, "\r\n--" + boundary + "\r\n");
   AppendStringToBody(postData, "Content-Disposition: form-data; name=\"current_price\"\r\n\r\n");
   AppendStringToBody(postData, DoubleToString(currentPrice, g_digits));

   //--- Part 5: entry_min
   AppendStringToBody(postData, "\r\n--" + boundary + "\r\n");
   AppendStringToBody(postData, "Content-Disposition: form-data; name=\"entry_min\"\r\n\r\n");
   AppendStringToBody(postData, DoubleToString(g_watchZoneMin, g_digits));

   //--- Part 6: entry_max
   AppendStringToBody(postData, "\r\n--" + boundary + "\r\n");
   AppendStringToBody(postData, "Content-Disposition: form-data; name=\"entry_max\"\r\n\r\n");
   AppendStringToBody(postData, DoubleToString(g_watchZoneMax, g_digits));

   //--- Part 7: M1 screenshot file
   AppendFilePart(postData, boundary, "screenshot_m1", fileM1);

   //--- Closing boundary
   AppendStringToBody(postData, "\r\n--" + boundary + "--\r\n");

   string headers = GetAuthHeader() + "Content-Type: multipart/form-data; boundary=" + boundary + "\r\n";
   char   result[];
   string resultHeaders;

   Print("Sending M1 confirmation to ", url, " (", ArraySize(postData), " bytes)...");

   int res = WebRequest("POST", url, headers, 30000, postData, result, resultHeaders);

   if(res != 200)
   {
      Print("ERROR: Confirmation request failed (HTTP ", res, ")");
      return false;
   }

   responseOut = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   Print("Confirmation response: ", responseOut);

   //--- Parse confirmed flag
   if(StringFind(responseOut, "\"confirmed\": true") >= 0 ||
      StringFind(responseOut, "\"confirmed\":true") >= 0)
   {
      Print("M1 confirmation: CONFIRMED!");
      return true;
   }

   Print("M1 confirmation: REJECTED");
   return false;
}

//+------------------------------------------------------------------+
//| Check if tracked positions have been closed (TP/SL hit)            |
//+------------------------------------------------------------------+
void CheckPositionClosures()
{
   if(g_posTicket1 == 0 && g_posTicket2 == 0)
      return;

   //--- Check TP1 position
   if(g_posTicket1 > 0)
   {
      if(!PositionSelectByTicket(g_posTicket1))
      {
         //--- Position no longer exists — it was closed
         //--- Check deal history to find out why and what the profit was
         string reason = "";
         double profit = 0;
         double closePrice = 0;

         if(FindCloseDeal(g_posTicket1, reason, profit, closePrice))
         {
            Print("TP1 position #", g_posTicket1, " closed: reason=", reason,
                  " profit=", DoubleToString(profit, 2), " price=", DoubleToString(closePrice, g_digits));
            SendCloseReport(g_posTradeId, g_posTicket1, closePrice, reason, profit);
         }
         else
         {
            Print("TP1 position #", g_posTicket1, " closed (could not determine reason)");
            SendCloseReport(g_posTradeId, g_posTicket1, 0, "unknown", 0);
         }

         g_posTicket1 = 0;

         //--- BREAK-EVEN: If TP1 was hit and TP2 is still open, move SL to entry
         if((reason == "tp1") && g_posTicket2 > 0 && g_tradeEntry > 0)
         {
            g_tp1Hit = true;
            MoveToBreakeven();
         }
      }
   }

   //--- Check TP2 position
   if(g_posTicket2 > 0)
   {
      if(!PositionSelectByTicket(g_posTicket2))
      {
         string reason = "";
         double profit = 0;
         double closePrice = 0;

         if(FindCloseDeal(g_posTicket2, reason, profit, closePrice))
         {
            Print("TP2 position #", g_posTicket2, " closed: reason=", reason,
                  " profit=", DoubleToString(profit, 2), " price=", DoubleToString(closePrice, g_digits));
            SendCloseReport(g_posTradeId, g_posTicket2, closePrice, reason, profit);
         }
         else
         {
            Print("TP2 position #", g_posTicket2, " closed (could not determine reason)");
            SendCloseReport(g_posTradeId, g_posTicket2, 0, "unknown", 0);
         }

         g_posTicket2 = 0;
      }
   }

   //--- If both positions closed, clear tracking
   if(g_posTicket1 == 0 && g_posTicket2 == 0)
   {
      g_posTradeId  = "";
      g_tradeEntry  = 0;
      g_tradeSL     = 0;
      g_tradeTP1    = 0;
      g_tradeTP2    = 0;
      g_tradeBias   = "";
      g_tp1Hit      = false;
   }
}

//+------------------------------------------------------------------+
//| Move TP2 stop-loss to break-even (entry price) after TP1 hit       |
//+------------------------------------------------------------------+
void MoveToBreakeven()
{
   if(g_posTicket2 == 0 || g_tradeEntry == 0)
      return;

   if(!PositionSelectByTicket(g_posTicket2))
   {
      Print("Break-even: TP2 position #", g_posTicket2, " not found (may have closed)");
      return;
   }

   double currentSL = PositionGetDouble(POSITION_SL);
   double currentTP = PositionGetDouble(POSITION_TP);
   double newSL     = NormalizeDouble(g_tradeEntry, g_digits);

   //--- Only move SL if it's actually an improvement
   bool shouldMove = false;
   if(g_tradeBias == "long" && newSL > currentSL)
      shouldMove = true;
   else if(g_tradeBias == "short" && (newSL < currentSL || currentSL == 0))
      shouldMove = true;

   if(shouldMove)
   {
      if(g_trade.PositionModify(g_posTicket2, newSL, currentTP))
         Print("BREAK-EVEN: TP2 #", g_posTicket2, " SL moved to entry ", DoubleToString(newSL, g_digits));
      else
         Print("WARNING: Break-even modify failed: ", g_trade.ResultRetcodeDescription());
   }
   else
   {
      Print("Break-even: SL already at or beyond entry (current=", DoubleToString(currentSL, g_digits),
            " entry=", DoubleToString(newSL, g_digits), ")");
   }
}

//+------------------------------------------------------------------+
//| Trailing stop management for TP2 runner                            |
//| When price reaches 75% of TP2 distance, trail SL to TP1 level     |
//+------------------------------------------------------------------+
void ManageTrailingStop()
{
   //--- Only manage if TP2 is open and TP1 has been hit (break-even already done)
   if(g_posTicket2 == 0 || g_tradeTP2 == 0 || !g_tp1Hit)
      return;

   if(!PositionSelectByTicket(g_posTicket2))
      return;

   //--- Get current market price
   double currentPrice;
   if(g_tradeBias == "long")
      currentPrice = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   else
      currentPrice = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   //--- Calculate progress toward TP2
   double totalDistance = MathAbs(g_tradeTP2 - g_tradeEntry);
   if(totalDistance == 0) return;

   double currentProgress;
   if(g_tradeBias == "long")
      currentProgress = currentPrice - g_tradeEntry;
   else
      currentProgress = g_tradeEntry - currentPrice;

   double progressPct = currentProgress / totalDistance;

   //--- When price reaches 75% of TP2 distance, trail SL to TP1 level
   if(progressPct >= 0.75)
   {
      double currentSL = PositionGetDouble(POSITION_SL);
      double currentTP = PositionGetDouble(POSITION_TP);
      double newSL     = NormalizeDouble(g_tradeTP1, g_digits);

      bool shouldMove = false;
      if(g_tradeBias == "long" && newSL > currentSL)
         shouldMove = true;
      else if(g_tradeBias == "short" && newSL < currentSL)
         shouldMove = true;

      if(shouldMove)
      {
         if(g_trade.PositionModify(g_posTicket2, newSL, currentTP))
            Print("TRAILING STOP: TP2 #", g_posTicket2, " SL trailed to TP1 level ",
                  DoubleToString(newSL, g_digits), " (progress: ", DoubleToString(progressPct * 100, 0), "%)");
         else
            Print("WARNING: Trailing stop modify failed: ", g_trade.ResultRetcodeDescription());
      }
   }
}

//+------------------------------------------------------------------+
//| Find the closing deal for a position and determine TP/SL/manual    |
//+------------------------------------------------------------------+
bool FindCloseDeal(ulong posTicket, string &reason, double &profit, double &closePrice)
{
   //--- Request recent deal history (last 24 hours)
   datetime from = TimeCurrent() - 86400;
   datetime to   = TimeCurrent() + 60;

   if(!HistorySelect(from, to))
      return false;

   int totalDeals = HistoryDealsTotal();

   for(int i = totalDeals - 1; i >= 0; i--)
   {
      ulong dealTicket = HistoryDealGetTicket(i);
      if(dealTicket == 0) continue;

      //--- Check if this deal closed our position
      ulong dealPos = HistoryDealGetInteger(dealTicket, DEAL_POSITION_ID);
      long dealEntry = HistoryDealGetInteger(dealTicket, DEAL_ENTRY);

      if(dealPos == posTicket && dealEntry == DEAL_ENTRY_OUT)
      {
         profit     = HistoryDealGetDouble(dealTicket, DEAL_PROFIT)
                    + HistoryDealGetDouble(dealTicket, DEAL_SWAP)
                    + HistoryDealGetDouble(dealTicket, DEAL_COMMISSION);
         closePrice = HistoryDealGetDouble(dealTicket, DEAL_PRICE);

         //--- Determine reason from deal comment
         string comment = HistoryDealGetString(dealTicket, DEAL_COMMENT);
         long dealReason = HistoryDealGetInteger(dealTicket, DEAL_REASON);

         if(StringFind(comment, "tp") >= 0 || StringFind(comment, "TP") >= 0
            || dealReason == DEAL_REASON_TP)
         {
            //--- Determine if TP1 or TP2 from our original order comment
            string origComment = HistoryDealGetString(dealTicket, DEAL_COMMENT);
            if(StringFind(origComment, "_TP1") >= 0)
               reason = "tp1";
            else if(StringFind(origComment, "_TP2") >= 0)
               reason = "tp2";
            else
               reason = "tp1";  // default to tp1 for first position
         }
         else if(StringFind(comment, "sl") >= 0 || StringFind(comment, "SL") >= 0
                 || dealReason == DEAL_REASON_SL)
         {
            reason = "sl";
         }
         else
         {
            reason = "manual";
         }

         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
//| Send close report to server                                        |
//+------------------------------------------------------------------+
void SendCloseReport(string tradeId, ulong ticket, double closePrice,
                     string reason, double profit)
{
   string url = InpServerBase + "/trade_closed";

   string json = "{";
   json += "\"trade_id\":\"" + tradeId + "\",";
   json += "\"symbol\":\"" + g_serverSymbol + "\",";
   json += "\"ticket\":" + IntegerToString(ticket) + ",";
   json += "\"close_price\":" + DoubleToString(closePrice, g_digits) + ",";
   json += "\"close_reason\":\"" + reason + "\",";
   json += "\"profit\":" + DoubleToString(profit, 2);
   json += "}";

   char postData[];
   StringToCharArray(json, postData, 0, WHOLE_ARRAY, CP_UTF8);
   ArrayResize(postData, ArraySize(postData) - 1);

   char   result[];
   string resultHeaders;
   string headers = GetAuthHeader() + "Content-Type: application/json\r\n";

   int res = WebRequest("POST", url, headers, 10000, postData, result, resultHeaders);
   if(res == 200)
      Print(_Symbol, " close report sent: ", reason, " profit=", DoubleToString(profit, 2));
   else
      Print("WARNING: Failed to send close report (HTTP ", res, ")");
}

//+------------------------------------------------------------------+
//| Chart event handler (for manual button)                            |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK && sparam == "btnManualScan")
   {
      //--- Only leaders can trigger scans
      if(g_isLeader)
      {
         Print("Manual scan button clicked!");
         RunAnalysis("Manual");
      }
      else
      {
         Print("Follower mode — scan not available. Trades come from leader.");
      }
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

   //--- Appearance: different style for leader vs follower
   if(g_isLeader)
   {
      ObjectSetString(0,  "btnManualScan", OBJPROP_TEXT, " Scan " + _Symbol + " ");
      ObjectSetInteger(0, "btnManualScan", OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0, "btnManualScan", OBJPROP_BGCOLOR, clrDodgerBlue);
      ObjectSetInteger(0, "btnManualScan", OBJPROP_BORDER_COLOR, clrDodgerBlue);
   }
   else
   {
      ObjectSetString(0,  "btnManualScan", OBJPROP_TEXT, " Follow " + g_serverSymbol + " ");
      ObjectSetInteger(0, "btnManualScan", OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0, "btnManualScan", OBJPROP_BGCOLOR, clrForestGreen);
      ObjectSetInteger(0, "btnManualScan", OBJPROP_BORDER_COLOR, clrForestGreen);
   }
   ObjectSetString(0,  "btnManualScan", OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, "btnManualScan", OBJPROP_FONTSIZE, 10);

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
//| Delete all AI_ visual objects from chart                           |
//+------------------------------------------------------------------+
void DeleteAllVisuals()
{
   ObjectsDeleteAll(0, "AI_");
   ChartRedraw(0);
}

//+------------------------------------------------------------------+
//| Draw entry zone rectangle + SL/TP lines + labels                   |
//+------------------------------------------------------------------+
void DrawEntryZone()
{
   if(!g_hasWatch || g_watchTradeId == "") return;

   //--- Use the chart's own timeframe for time coordinates (avoids 0 when higher TF data not loaded)
   ENUM_TIMEFRAMES chartTF = ChartPeriod(0);
   int barsBack    = 50;    // How far left the zone extends
   int barsForward = 30;    // How far right the zone extends
   int barsLabel   = 5;     // Where the label sits

   datetime timeStart = iTime(_Symbol, chartTF, barsBack);
   datetime timeEnd   = iTime(_Symbol, chartTF, 0) + barsForward * PeriodSeconds(chartTF);
   datetime timeLbl   = iTime(_Symbol, chartTF, barsLabel);

   //--- Safety: if iTime returned 0 (data not loaded), fall back to TimeCurrent arithmetic
   if(timeStart == 0 || timeEnd == 0)
   {
      timeStart = TimeCurrent() - barsBack * PeriodSeconds(chartTF);
      timeEnd   = TimeCurrent() + barsForward * PeriodSeconds(chartTF);
      timeLbl   = TimeCurrent() - barsLabel * PeriodSeconds(chartTF);
   }

   //--- Determine colors based on bias
   color zoneColor, zoneBorder;
   if(g_watchBias == "long")
   {
      zoneColor   = C'51,153,255';   // Blue
      zoneBorder  = C'30,120,220';
   }
   else
   {
      zoneColor   = C'255,82,82';    // Red
      zoneBorder  = C'220,50,50';
   }

   //--- Entry zone rectangle (filled, drawn behind candles)
   ObjectDelete(0, "AI_Zone");
   if(!ObjectCreate(0, "AI_Zone", OBJ_RECTANGLE, 0, timeStart, g_watchZoneMin, timeEnd, g_watchZoneMax))
      Print("WARNING: Failed to create AI_Zone (error ", GetLastError(), ")");
   ObjectSetInteger(0, "AI_Zone", OBJPROP_COLOR, zoneColor);
   ObjectSetInteger(0, "AI_Zone", OBJPROP_FILL, true);
   ObjectSetInteger(0, "AI_Zone", OBJPROP_BACK, true);
   ObjectSetInteger(0, "AI_Zone", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_Zone", OBJPROP_HIDDEN, true);

   //--- Zone border rectangle (unfilled outline in front of candles for visibility)
   ObjectDelete(0, "AI_ZoneBorder");
   ObjectCreate(0, "AI_ZoneBorder", OBJ_RECTANGLE, 0, timeStart, g_watchZoneMin, timeEnd, g_watchZoneMax);
   ObjectSetInteger(0, "AI_ZoneBorder", OBJPROP_COLOR, zoneBorder);
   ObjectSetInteger(0, "AI_ZoneBorder", OBJPROP_FILL, false);
   ObjectSetInteger(0, "AI_ZoneBorder", OBJPROP_BACK, false);
   ObjectSetInteger(0, "AI_ZoneBorder", OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, "AI_ZoneBorder", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_ZoneBorder", OBJPROP_HIDDEN, true);

   //--- Zone label text: "SHORT | 8/12 | HIGH | R:R 1:2.5"
   string biasStr = (g_watchBias == "long") ? "LONG" : "SHORT";
   string rrStr   = (g_watchRRatio > 0) ? ("R:R 1:" + DoubleToString(g_watchRRatio, 1)) : "";
   string zoneText = biasStr + " Entry Zone";
   if(g_watchChecklistScore != "")
      zoneText += " | " + g_watchChecklistScore;
   if(g_watchConfidence != "")
      zoneText += " | " + g_watchConfidence;
   if(rrStr != "")
      zoneText += " | " + rrStr;

   double zoneMid = (g_watchZoneMin + g_watchZoneMax) / 2.0;
   ObjectDelete(0, "AI_LblZone");
   ObjectCreate(0, "AI_LblZone", OBJ_TEXT, 0, timeLbl, zoneMid);
   ObjectSetString(0, "AI_LblZone", OBJPROP_TEXT, zoneText);
   ObjectSetString(0, "AI_LblZone", OBJPROP_FONT, "Arial Bold");
   ObjectSetInteger(0, "AI_LblZone", OBJPROP_FONTSIZE, 9);
   ObjectSetInteger(0, "AI_LblZone", OBJPROP_COLOR, zoneColor);
   ObjectSetInteger(0, "AI_LblZone", OBJPROP_ANCHOR, ANCHOR_CENTER);
   ObjectSetInteger(0, "AI_LblZone", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_LblZone", OBJPROP_HIDDEN, true);

   //--- Entry midpoint for pip calculations
   double entryMid = (g_watchZoneMin + g_watchZoneMax) / 2.0;

   //--- SL line (red dashed, width 2, in front of candles)
   double slPips = MathAbs(g_watchSL - entryMid) / g_pipSize;
   ObjectDelete(0, "AI_LineSL");
   ObjectCreate(0, "AI_LineSL", OBJ_HLINE, 0, 0, g_watchSL);
   ObjectSetInteger(0, "AI_LineSL", OBJPROP_COLOR, clrRed);
   ObjectSetInteger(0, "AI_LineSL", OBJPROP_STYLE, STYLE_DASH);
   ObjectSetInteger(0, "AI_LineSL", OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, "AI_LineSL", OBJPROP_BACK, false);
   ObjectSetInteger(0, "AI_LineSL", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_LineSL", OBJPROP_HIDDEN, true);

   //--- SL label
   ObjectDelete(0, "AI_LblSL");
   ObjectCreate(0, "AI_LblSL", OBJ_TEXT, 0, timeLbl, g_watchSL);
   ObjectSetString(0, "AI_LblSL", OBJPROP_TEXT, "SL  " + DoubleToString(g_watchSL, g_digits) + "  (" + DoubleToString(slPips, 1) + " pips)");
   ObjectSetString(0, "AI_LblSL", OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, "AI_LblSL", OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, "AI_LblSL", OBJPROP_COLOR, clrRed);
   ObjectSetInteger(0, "AI_LblSL", OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, "AI_LblSL", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_LblSL", OBJPROP_HIDDEN, true);

   //--- TP1 line (green dashed, width 2, in front of candles)
   double tp1Pips = MathAbs(g_watchTP1 - entryMid) / g_pipSize;
   ObjectDelete(0, "AI_LineTP1");
   ObjectCreate(0, "AI_LineTP1", OBJ_HLINE, 0, 0, g_watchTP1);
   ObjectSetInteger(0, "AI_LineTP1", OBJPROP_COLOR, clrLimeGreen);
   ObjectSetInteger(0, "AI_LineTP1", OBJPROP_STYLE, STYLE_DASH);
   ObjectSetInteger(0, "AI_LineTP1", OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, "AI_LineTP1", OBJPROP_BACK, false);
   ObjectSetInteger(0, "AI_LineTP1", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_LineTP1", OBJPROP_HIDDEN, true);

   //--- TP1 label
   ObjectDelete(0, "AI_LblTP1");
   ObjectCreate(0, "AI_LblTP1", OBJ_TEXT, 0, timeLbl, g_watchTP1);
   ObjectSetString(0, "AI_LblTP1", OBJPROP_TEXT, "TP1  " + DoubleToString(g_watchTP1, g_digits) + "  (+" + DoubleToString(tp1Pips, 1) + " pips)");
   ObjectSetString(0, "AI_LblTP1", OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, "AI_LblTP1", OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, "AI_LblTP1", OBJPROP_COLOR, clrLimeGreen);
   ObjectSetInteger(0, "AI_LblTP1", OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, "AI_LblTP1", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_LblTP1", OBJPROP_HIDDEN, true);

   //--- TP2 line (green solid, width 2, in front of candles)
   double tp2Pips = MathAbs(g_watchTP2 - entryMid) / g_pipSize;
   ObjectDelete(0, "AI_LineTP2");
   ObjectCreate(0, "AI_LineTP2", OBJ_HLINE, 0, 0, g_watchTP2);
   ObjectSetInteger(0, "AI_LineTP2", OBJPROP_COLOR, clrGreen);
   ObjectSetInteger(0, "AI_LineTP2", OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, "AI_LineTP2", OBJPROP_WIDTH, 2);
   ObjectSetInteger(0, "AI_LineTP2", OBJPROP_BACK, false);
   ObjectSetInteger(0, "AI_LineTP2", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_LineTP2", OBJPROP_HIDDEN, true);

   //--- TP2 label
   ObjectDelete(0, "AI_LblTP2");
   ObjectCreate(0, "AI_LblTP2", OBJ_TEXT, 0, timeLbl, g_watchTP2);
   ObjectSetString(0, "AI_LblTP2", OBJPROP_TEXT, "TP2  " + DoubleToString(g_watchTP2, g_digits) + "  (+" + DoubleToString(tp2Pips, 1) + " pips)");
   ObjectSetString(0, "AI_LblTP2", OBJPROP_FONT, "Arial");
   ObjectSetInteger(0, "AI_LblTP2", OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, "AI_LblTP2", OBJPROP_COLOR, clrGreen);
   ObjectSetInteger(0, "AI_LblTP2", OBJPROP_ANCHOR, ANCHOR_LEFT);
   ObjectSetInteger(0, "AI_LblTP2", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_LblTP2", OBJPROP_HIDDEN, true);
}

//+------------------------------------------------------------------+
//| Draw info panel (top-right corner dashboard)                       |
//+------------------------------------------------------------------+
void DrawInfoPanel()
{
   //--- Panel dimensions
   int panelX = 15;    // Distance from right edge
   int panelY = 30;    // Distance from top edge
   int panelW = 260;
   int lineH  = 22;    // Line height in pixels
   int padX   = 12;    // Text padding from panel left
   int padY   = 8;     // Text padding from panel top

   //--- Determine panel height based on content
   int numLines = 11;  // Title + separator + 9 data lines
   int panelH   = padY * 2 + numLines * lineH;

   //--- Background rectangle
   ObjectDelete(0, "AI_PanelBG");
   ObjectCreate(0, "AI_PanelBG", OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_CORNER, CORNER_RIGHT_UPPER);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_XDISTANCE, panelX);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_YDISTANCE, panelY);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_XSIZE, panelW);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_YSIZE, panelH);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_BGCOLOR, C'30,30,35');
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_BORDER_COLOR, C'80,80,90');
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_BACK, false);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "AI_PanelBG", OBJPROP_HIDDEN, true);

   //--- Helper variables for label positioning
   //--- In CORNER_RIGHT_UPPER, XDISTANCE is from the right edge, so
   //--- text needs to be just inside the panel's right edge (panel starts at panelX from right)
   int textX = panelX + panelW - padX;  // Left edge of text inside panel
   int curY  = panelY + padY;

   //--- Title: "AI Trade Analyst"
   CreatePanelLabel("AI_PanelTitle", textX, curY, "AI Trade Analyst", "Arial Bold", 11, clrWhite);
   curY += lineH;

   //--- Separator line (using thin text)
   CreatePanelLabel("AI_PanelSep", textX, curY, "--------------------------------------", "Arial", 7, C'80,80,90');
   curY += (int)(lineH * 0.7);

   //--- Status
   string statusText = "NO SETUP";
   color  statusClr  = C'120,120,130';
   if(g_hasWatch)
   {
      statusText = "WATCHING";
      statusClr  = clrCyan;
   }
   CreatePanelLabel("AI_PanelStatus", textX, curY, "Status:  " + statusText, "Arial Bold", 10, statusClr);
   curY += lineH;

   if(!g_hasWatch)
   {
      //--- No watch — just show status, done
      ChartRedraw(0);
      return;
   }

   //--- Bias (color-coded)
   string biasStr = (g_watchBias == "long") ? "LONG" : "SHORT";
   color  biasClr = (g_watchBias == "long") ? C'51,153,255' : C'255,82,82';
   CreatePanelLabel("AI_PanelBias", textX, curY, "Bias:  " + biasStr, "Arial Bold", 10, biasClr);
   curY += lineH;

   //--- Checklist Score
   color scoreClr = clrYellow;
   CreatePanelLabel("AI_PanelScore", textX, curY, "Checklist:  " + g_watchChecklistScore, "Arial", 9, scoreClr);
   curY += lineH;

   //--- Confidence (4 tiers: HIGH, MEDIUM_HIGH, MEDIUM, LOW)
   color confClr = clrLimeGreen;
   string confDisplay = g_watchConfidence;
   if(g_watchConfidence == "low" || g_watchConfidence == "LOW")
   {  confClr = clrOrangeRed; confDisplay = "LOW"; }
   else if(g_watchConfidence == "medium" || g_watchConfidence == "MEDIUM")
   {  confClr = clrOrange; confDisplay = "MEDIUM"; }
   else if(g_watchConfidence == "medium_high" || g_watchConfidence == "MEDIUM_HIGH")
   {  confClr = clrYellow; confDisplay = "MED-HIGH"; }
   else
   {  confClr = clrLimeGreen; confDisplay = "HIGH"; }
   CreatePanelLabel("AI_PanelConf", textX, curY, "Confidence:  " + confDisplay, "Arial", 9, confClr);
   curY += lineH;

   //--- R:R Ratio
   string rrText = (g_watchRRatio > 0) ? ("1 : " + DoubleToString(g_watchRRatio, 1)) : "N/A";
   CreatePanelLabel("AI_PanelRR", textX, curY, "R:R:  " + rrText, "Arial", 9, clrOrange);
   curY += lineH;

   //--- Entry Zone
   string entryText = DoubleToString(g_watchZoneMin, g_digits) + " - " + DoubleToString(g_watchZoneMax, g_digits);
   CreatePanelLabel("AI_PanelEntry", textX, curY, "Entry:  " + entryText, "Arial", 9, clrWhite);
   curY += lineH;

   //--- SL
   CreatePanelLabel("AI_PanelSL", textX, curY, "SL:  " + DoubleToString(g_watchSL, g_digits) + "  (" + DoubleToString(g_watchSlPips, 1) + "p)", "Arial", 9, clrRed);
   curY += lineH;

   //--- TP1
   double tp1Pips = MathAbs(g_watchTP1 - (g_watchZoneMin + g_watchZoneMax) / 2.0) / g_pipSize;
   CreatePanelLabel("AI_PanelTP1", textX, curY, "TP1:  " + DoubleToString(g_watchTP1, g_digits) + "  (+" + DoubleToString(tp1Pips, 1) + "p)", "Arial", 9, clrLimeGreen);
   curY += lineH;

   //--- TP2
   double tp2Pips = MathAbs(g_watchTP2 - (g_watchZoneMin + g_watchZoneMax) / 2.0) / g_pipSize;
   CreatePanelLabel("AI_PanelTP2", textX, curY, "TP2:  " + DoubleToString(g_watchTP2, g_digits) + "  (+" + DoubleToString(tp2Pips, 1) + "p)", "Arial", 9, clrGreen);
}

//+------------------------------------------------------------------+
//| Helper: create a screen-anchored label for the info panel          |
//+------------------------------------------------------------------+
void CreatePanelLabel(string name, int x, int y, string text, string font, int fontSize, color clr)
{
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_RIGHT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, font);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, fontSize);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
}

//+------------------------------------------------------------------+
//| Update all chart visuals (called from OnTimer)                     |
//+------------------------------------------------------------------+
void UpdateVisuals()
{
   if(g_hasWatch && g_watchTradeId != "")
   {
      DrawEntryZone();
      DrawInfoPanel();
      ChartRedraw(0);
   }
   else
   {
      //--- Only delete if objects still exist
      if(ObjectFind(0, "AI_PanelBG") >= 0 || ObjectFind(0, "AI_Zone") >= 0)
         DeleteAllVisuals();
   }
}

//+------------------------------------------------------------------+
//| Main analysis function                                             |
//+------------------------------------------------------------------+
bool RunAnalysis(string session)
{
   Print("=== Starting ", session, " session analysis for ", _Symbol, " ===");

   //--- Step 1: Capture screenshots for D1, H4, H1, M5 (top-down multi-timeframe)
   string fileD1  = CaptureTimeframeScreenshot(PERIOD_D1, "D1");
   string fileH4  = CaptureTimeframeScreenshot(PERIOD_H4, "H4");
   string fileH1  = CaptureTimeframeScreenshot(PERIOD_H1, "H1");
   string fileM5  = CaptureTimeframeScreenshot(PERIOD_M5, "M5");

   if(fileD1 == "" || fileH4 == "" || fileH1 == "" || fileM5 == "")
   {
      Print("ERROR: Failed to capture one or more screenshots.");
      return false;
   }

   Print("All screenshots captured successfully (D1, H4, H1, M5).");

   //--- Step 2: Gather market data JSON
   string jsonData = BuildMarketDataJSON(session);
   Print("Market data JSON built (", StringLen(jsonData), " chars).");

   //--- Step 3: Send to server
   bool result = SendToServer(fileD1, fileH4, fileH1, fileM5, jsonData);

   if(result)
   {
      g_lastScanTime = TimeCurrent();
      Print("=== ", _Symbol, " ", session, " analysis sent successfully ===");
   }
   else
   {
      Print("ERROR: Failed to send analysis to server.");
   }

   //--- Cleanup screenshot files
   FileDelete(fileD1);
   FileDelete(fileH4);
   FileDelete(fileH1);
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

   Print("Opened temp chart ", chartId, " for ", _Symbol, " ", tfLabel);

   //--- Prevent temp chart from stealing focus from main chart
   ChartSetInteger(chartId, CHART_BRING_TO_TOP, false);

   //--- Wait for chart to fully initialize
   ChartRedraw(chartId);
   Sleep(500);

   //--- Apply saved template from main chart (most reliable method)
   if(!ChartApplyTemplate(chartId, g_templateName))
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

   //--- Symbol info (use g_serverSymbol for server-facing name)
   json += "\"symbol\":\"" + g_serverSymbol + "\",";
   json += "\"session\":\"" + session + "\",";
   json += "\"timestamp\":\"" + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "\",";

   //--- Bid/Ask/Spread
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double spread = (ask - bid) / g_pipSize;  // Spread in pips (universal)

   json += "\"bid\":" + DoubleToString(bid, g_digits) + ",";
   json += "\"ask\":" + DoubleToString(ask, g_digits) + ",";
   json += "\"spread_pips\":" + DoubleToString(spread, 1) + ",";

   //--- ATR values (D1, H4, H1, M5)
   json += "\"atr_d1\":" + DoubleToString(GetATR(PERIOD_D1, 14), g_digits) + ",";
   json += "\"atr_h4\":" + DoubleToString(GetATR(PERIOD_H4, 14), g_digits) + ",";
   json += "\"atr_h1\":" + DoubleToString(GetATR(PERIOD_H1, 14), g_digits) + ",";
   json += "\"atr_m5\":" + DoubleToString(GetATR(PERIOD_M5, 14), g_digits) + ",";

   //--- Daily range (today)
   double dayHigh = iHigh(_Symbol, PERIOD_D1, 0);
   double dayLow  = iLow(_Symbol, PERIOD_D1, 0);
   double dayRange = (dayHigh - dayLow) / g_pipSize;
   json += "\"daily_high\":" + DoubleToString(dayHigh, g_digits) + ",";
   json += "\"daily_low\":" + DoubleToString(dayLow, g_digits) + ",";
   json += "\"daily_range_pips\":" + DoubleToString(dayRange, 1) + ",";

   //--- Previous day levels (critical ICT levels)
   json += "\"prev_day_high\":" + DoubleToString(iHigh(_Symbol, PERIOD_D1, 1), g_digits) + ",";
   json += "\"prev_day_low\":" + DoubleToString(iLow(_Symbol, PERIOD_D1, 1), g_digits) + ",";
   json += "\"prev_day_close\":" + DoubleToString(iClose(_Symbol, PERIOD_D1, 1), g_digits) + ",";

   //--- Current week levels
   json += "\"week_high\":" + DoubleToString(iHigh(_Symbol, PERIOD_W1, 0), g_digits) + ",";
   json += "\"week_low\":" + DoubleToString(iLow(_Symbol, PERIOD_W1, 0), g_digits) + ",";

   //--- Asian session range (00:00-08:00 CET)
   double asianHigh = 0, asianLow = 0;
   if(GetSessionRange(0, 8, asianHigh, asianLow))
   {
      json += "\"asian_high\":" + DoubleToString(asianHigh, g_digits) + ",";
      json += "\"asian_low\":" + DoubleToString(asianLow, g_digits) + ",";
   }

   //--- RSI values (14-period)
   json += "\"rsi_d1\":" + DoubleToString(GetRSI(PERIOD_D1, 14), 1) + ",";
   json += "\"rsi_h4\":" + DoubleToString(GetRSI(PERIOD_H4, 14), 1) + ",";
   json += "\"rsi_h1\":" + DoubleToString(GetRSI(PERIOD_H1, 14), 1) + ",";
   json += "\"rsi_m5\":" + DoubleToString(GetRSI(PERIOD_M5, 14), 1) + ",";

   //--- Account balance
   json += "\"account_balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";

   //--- OHLC data (D1=20 bars ~1 month, H4=30 bars ~5 days, H1=50 bars ~2 days, M5=60 bars ~5 hours)
   json += "\"ohlc_d1\":" + GetOHLCArray(PERIOD_D1, 20) + ",";
   json += "\"ohlc_h4\":" + GetOHLCArray(PERIOD_H4, 30) + ",";
   json += "\"ohlc_h1\":" + GetOHLCArray(PERIOD_H1, 50) + ",";
   json += "\"ohlc_m5\":" + GetOHLCArray(PERIOD_M5, 60);

   json += "}";
   return json;
}

//+------------------------------------------------------------------+
//| Map timeframe to pre-created ATR handle                           |
//+------------------------------------------------------------------+
int GetATRHandle(ENUM_TIMEFRAMES tf)
{
   if(tf == PERIOD_D1) return g_hATR_D1;
   if(tf == PERIOD_H4) return g_hATR_H4;
   if(tf == PERIOD_H1) return g_hATR_H1;
   if(tf == PERIOD_M5) return g_hATR_M5;
   return INVALID_HANDLE;
}

//+------------------------------------------------------------------+
//| Map timeframe to pre-created RSI handle                           |
//+------------------------------------------------------------------+
int GetRSIHandle(ENUM_TIMEFRAMES tf)
{
   if(tf == PERIOD_D1) return g_hRSI_D1;
   if(tf == PERIOD_H4) return g_hRSI_H4;
   if(tf == PERIOD_H1) return g_hRSI_H1;
   if(tf == PERIOD_M5) return g_hRSI_M5;
   return INVALID_HANDLE;
}

//+------------------------------------------------------------------+
//| Get ATR value for a timeframe (uses pre-created handle)           |
//+------------------------------------------------------------------+
double GetATR(ENUM_TIMEFRAMES tf, int period)
{
   int handle = GetATRHandle(tf);
   if(handle == INVALID_HANDLE)
   {
      Print("ERROR: No ATR handle for ", EnumToString(tf));
      return 0;
   }

   double atrBuffer[];
   ArraySetAsSeries(atrBuffer, true);

   if(CopyBuffer(handle, 0, 0, 1, atrBuffer) <= 0)
   {
      Print("ERROR: Failed to copy ATR buffer for ", EnumToString(tf));
      return 0;
   }

   return atrBuffer[0];
}

//+------------------------------------------------------------------+
//| Get RSI value for a timeframe (uses pre-created handle)           |
//+------------------------------------------------------------------+
double GetRSI(ENUM_TIMEFRAMES tf, int period)
{
   int handle = GetRSIHandle(tf);
   if(handle == INVALID_HANDLE)
   {
      Print("ERROR: No RSI handle for ", EnumToString(tf));
      return 0;
   }

   double rsiBuffer[];
   ArraySetAsSeries(rsiBuffer, true);

   if(CopyBuffer(handle, 0, 0, 1, rsiBuffer) <= 0)
   {
      Print("ERROR: Failed to copy RSI buffer for ", EnumToString(tf));
      return 0;
   }

   return rsiBuffer[0];
}

//+------------------------------------------------------------------+
//| Get session high/low range (using CET hours, reads H1 bars)        |
//+------------------------------------------------------------------+
bool GetSessionRange(int startCetHour, int endCetHour, double &high, double &low)
{
   //--- Get midnight (server time) of today
   MqlDateTime dtNow;
   TimeCurrent(dtNow);
   dtNow.hour = 0;
   dtNow.min = 0;
   dtNow.sec = 0;
   datetime midnight = StructToTime(dtNow);

   //--- Convert CET hours to server time
   int startServerHour = startCetHour + InpTimezoneOffset;
   int endServerHour   = endCetHour + InpTimezoneOffset;

   datetime tStart = midnight + startServerHour * 3600;
   datetime tEnd   = midnight + endServerHour * 3600;

   //--- Get H1 bars within the time range
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(_Symbol, PERIOD_H1, tStart, tEnd, rates);
   if(copied <= 0) return false;

   high = rates[0].high;
   low  = rates[0].low;
   for(int i = 1; i < copied; i++)
   {
      if(rates[i].high > high) high = rates[i].high;
      if(rates[i].low < low)   low  = rates[i].low;
   }
   return true;
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
      json += "\"open\":" + DoubleToString(rates[i].open, g_digits) + ",";
      json += "\"high\":" + DoubleToString(rates[i].high, g_digits) + ",";
      json += "\"low\":" + DoubleToString(rates[i].low, g_digits) + ",";
      json += "\"close\":" + DoubleToString(rates[i].close, g_digits) + ",";
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
bool SendToServer(string fileD1, string fileH4, string fileH1, string fileM5, string &jsonData)
{
   string boundary = "----AIBound" + IntegerToString(GetTickCount());

   //--- Build multipart body using char[] (what WebRequest expects)
   char postData[];

   //--- Part 1: market_data (text form field — no Content-Type header)
   AppendStringToBody(postData, "--" + boundary + "\r\n");
   AppendStringToBody(postData, "Content-Disposition: form-data; name=\"market_data\"\r\n");
   AppendStringToBody(postData, "\r\n");
   AppendStringToBody(postData, jsonData);

   //--- Part 2-5: screenshot files (D1, H4, H1, M5 — top-down order)
   AppendFilePart(postData, boundary, "screenshot_d1", fileD1);
   AppendFilePart(postData, boundary, "screenshot_h4", fileH4);
   AppendFilePart(postData, boundary, "screenshot_h1", fileH1);
   AppendFilePart(postData, boundary, "screenshot_m5", fileM5);

   //--- Closing boundary
   AppendStringToBody(postData, "\r\n--" + boundary + "--\r\n");

   //--- Prepare headers
   string headers = GetAuthHeader() + "Content-Type: multipart/form-data; boundary=" + boundary + "\r\n";

   //--- Send the request
   char   result[];
   string resultHeaders;
   int timeout = 60000; // 60 second timeout

   Print("Sending ", _Symbol, " to ", InpServerURL, " (", ArraySize(postData), " bytes)...");

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
//| Poll server for pending trades (symbol-aware)                      |
//+------------------------------------------------------------------+
void PollPendingTrade()
{
   string url = InpServerBase + "/pending_trade?symbol=" + g_serverSymbol;
   char   postData[];  // empty — this is GET
   char   result[];
   string resultHeaders;
   string headers = GetAuthHeader();

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

   Print("=== ", _Symbol, " pending trade found! ===");
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

   //--- Parse adaptive TP1 close percentage (default 50% if not present)
   double tp1ClosePct = JsonGetDouble(response, "tp1_close_pct");
   if(tp1ClosePct <= 0 || tp1ClosePct > 100)
      tp1ClosePct = 50.0;
   g_tp1ClosePct = tp1ClosePct;

   //--- Execute the trade
   ExecuteTrade(tradeId, bias, entryMin, entryMax, stopLoss, tp1, tp2, slPips);
}

// CheckLimitOrderExpiry removed in v6.0 — replaced by zone watching + M1 confirmation

//+------------------------------------------------------------------+
//| Execute trade with split lots (50% TP1, 50% TP2)                   |
//| Uses LIMIT orders when price is outside entry zone                 |
//| Uses MARKET orders when price is already in the zone               |
//+------------------------------------------------------------------+
void ExecuteTrade(string tradeId, string bias, double entryMin, double entryMax,
                  double stopLoss, double tp1, double tp2, double slPips)
{
   //--- v6.0: Market orders ONLY (M1 confirmation already verified price is at zone)
   Print("Executing ", _Symbol, " trade: ", bias, " ID=", tradeId,
         " (M1 confirmed at zone)");

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

   //--- Split into two positions using server-calculated TP1 close %
   double lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxLot  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   double tp1Ratio = g_tp1ClosePct / 100.0;
   double lotsTP1 = MathFloor((totalLots * tp1Ratio) / lotStep) * lotStep;
   double lotsTP2 = MathFloor((totalLots * (1.0 - tp1Ratio)) / lotStep) * lotStep;

   if(lotsTP1 < minLot) lotsTP1 = minLot;
   if(lotsTP2 < minLot) lotsTP2 = minLot;
   if(lotsTP1 > maxLot) lotsTP1 = maxLot;
   if(lotsTP2 > maxLot) lotsTP2 = maxLot;

   Print("Lot size: total=", DoubleToString(totalLots, 2),
         " TP1=", DoubleToString(lotsTP1, 2),
         " TP2=", DoubleToString(lotsTP2, 2));

   //--- Place MARKET orders (price is in the zone, M1 confirmed reaction)
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
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

   bool ok1, ok2;
   ulong ticket1 = 0, ticket2 = 0;
   double actualEntry = 0;

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
   Print("TP1 MARKET order filled: ticket=", ticket1, " entry=", DoubleToString(actualEntry, g_digits));

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
   Print("TP2 MARKET order filled: ticket=", ticket2, " entry=", DoubleToString(actualEntry2, g_digits));

   Print("=== ", _Symbol, " MARKET orders filled: ", bias, " ", DoubleToString(lotsTP1 + lotsTP2, 2), " lots ===");

   //--- Track positions for close detection
   g_posTicket1 = ticket1;
   g_posTicket2 = ticket2;
   g_posTradeId = tradeId;

   //--- Store trade levels for break-even and trailing stop management
   g_tradeEntry = actualEntry;
   g_tradeSL    = stopLoss;
   g_tradeTP1   = tp1;
   g_tradeTP2   = tp2;
   g_tradeBias  = bias;
   g_tp1Hit     = false;

   SendExecutionReport(tradeId, (int)ticket1, (int)ticket2, lotsTP1, lotsTP2,
                       actualEntry, stopLoss, tp1, tp2, "executed", "");
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

   if(tickSize == 0 || slPips == 0)
      return 0;

   //--- Universal pip value calculation
   //--- g_pipSize is set in OnInit based on symbol digits
   double pipValue = (g_pipSize / tickSize) * tickValue;  // value of 1 pip per 1 lot

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
//| Send execution report back to server (with symbol)                 |
//+------------------------------------------------------------------+
void SendExecutionReport(string tradeId, int ticket1, int ticket2,
                         double lots1, double lots2, double entry,
                         double sl, double actualTp1, double actualTp2,
                         string status, string errorMsg)
{
   string url = InpServerBase + "/trade_executed";

   //--- Build JSON body (use g_serverSymbol so server matches the correct trade)
   string json = "{";
   json += "\"trade_id\":\"" + tradeId + "\",";
   json += "\"symbol\":\"" + g_serverSymbol + "\",";
   json += "\"ticket_tp1\":" + IntegerToString(ticket1) + ",";
   json += "\"ticket_tp2\":" + IntegerToString(ticket2) + ",";
   json += "\"lots_tp1\":" + DoubleToString(lots1, 2) + ",";
   json += "\"lots_tp2\":" + DoubleToString(lots2, 2) + ",";
   json += "\"actual_entry\":" + DoubleToString(entry, g_digits) + ",";
   json += "\"actual_sl\":" + DoubleToString(sl, g_digits) + ",";
   json += "\"actual_tp1\":" + DoubleToString(actualTp1, g_digits) + ",";
   json += "\"actual_tp2\":" + DoubleToString(actualTp2, g_digits) + ",";
   json += "\"status\":\"" + status + "\",";
   json += "\"error_message\":\"" + errorMsg + "\"";
   json += "}";

   char postData[];
   StringToCharArray(json, postData, 0, WHOLE_ARRAY, CP_UTF8);
   // Strip null terminator
   ArrayResize(postData, ArraySize(postData) - 1);

   char   result[];
   string resultHeaders;
   string headers = GetAuthHeader() + "Content-Type: application/json\r\n";

   int res = WebRequest("POST", url, headers, 10000, postData, result, resultHeaders);
   if(res == 200)
      Print(_Symbol, " execution report sent to server: ", status);
   else
      Print("WARNING: Failed to send ", _Symbol, " execution report (HTTP ", res, ")");
}

//+------------------------------------------------------------------+
//| Find exact JSON key position (not a suffix of a longer key)        |
//| e.g. searching for "id" must NOT match "trade_id"                  |
//+------------------------------------------------------------------+
int JsonFindKey(string json, string key)
{
   string search = "\"" + key + "\"";
   int pos = 0;
   while(true)
   {
      pos = StringFind(json, search, pos);
      if(pos < 0) return -1;

      //--- Verify this is an exact key: the char before the opening quote
      //--- must be { , or whitespace (not a letter/underscore from a longer key)
      if(pos > 0)
      {
         ushort prevChar = StringGetCharacter(json, pos - 1);
         // If previous char is alphanumeric or underscore, this is a partial match
         if((prevChar >= 'a' && prevChar <= 'z') || (prevChar >= 'A' && prevChar <= 'Z') ||
            (prevChar >= '0' && prevChar <= '9') || prevChar == '_')
         {
            pos += StringLen(search);
            continue;  // Skip this match, search further
         }
      }
      return pos;
   }
   return -1;
}

//+------------------------------------------------------------------+
//| Simple JSON string parser — extract string value by key            |
//+------------------------------------------------------------------+
string JsonGetString(string json, string key)
{
   int pos = JsonFindKey(json, key);
   if(pos < 0) return "";

   string search = "\"" + key + "\"";

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
   int pos = JsonFindKey(json, key);
   if(pos < 0) return 0;

   string search = "\"" + key + "\"";

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

