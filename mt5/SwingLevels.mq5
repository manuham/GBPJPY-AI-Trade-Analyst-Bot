//+------------------------------------------------------------------+
//|                                                  SwingLevels.mq5 |
//|                         Swing High/Low Horizontal Level Indicator |
//|                         Draws clean S/R lines for AI analysis     |
//+------------------------------------------------------------------+
#property copyright "GBPJPY AI Trade Analyst Bot"
#property link      ""
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots 0

//--- Input parameters
input int      InpLookback     = 3;          // Swing Lookback (bars each side)
input int      InpMaxLines     = 15;         // Max Lines on Chart
input color    InpHighColor    = clrRed;     // Swing High Line Color
input color    InpLowColor     = clrDodgerBlue; // Swing Low Line Color
input color    InpLabelColor   = clrDimGray; // Price Label Color
input int      InpLabelSize    = 7;          // Price Label Font Size
input bool     InpRemoveBroken = true;       // Remove Broken Levels

//--- Prefix for all objects created by this indicator
string g_prefix = "SWL_";

//+------------------------------------------------------------------+
//| Custom indicator initialization function                          |
//+------------------------------------------------------------------+
int OnInit()
{
   Print("SwingLevels indicator initialized (lookback=", InpLookback,
         ", max=", InpMaxLines, ")");
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Custom indicator deinitialization function                         |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   //--- Remove all objects created by this indicator
   ObjectsDeleteAll(0, g_prefix);
}

//+------------------------------------------------------------------+
//| Custom indicator iteration function                                |
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
{
   if(rates_total < InpLookback * 2 + 1)
      return rates_total;

   //--- Set arrays as series (newest = index 0)
   ArraySetAsSeries(time, true);
   ArraySetAsSeries(high, true);
   ArraySetAsSeries(low, true);
   ArraySetAsSeries(close, true);

   //--- Clear all existing objects
   ObjectsDeleteAll(0, g_prefix);

   //--- Scan for swing highs and lows
   double swingHighs[];
   double swingLows[];
   datetime swingHighTimes[];
   datetime swingLowTimes[];

   ArrayResize(swingHighs, 0);
   ArrayResize(swingLows, 0);
   ArrayResize(swingHighTimes, 0);
   ArrayResize(swingLowTimes, 0);

   //--- Start from lookback offset (need N bars to the right to confirm)
   for(int i = InpLookback; i < rates_total - InpLookback; i++)
   {
      //--- Check swing high
      if(IsSwingHigh(high, i, InpLookback))
      {
         int size = ArraySize(swingHighs);
         ArrayResize(swingHighs, size + 1);
         ArrayResize(swingHighTimes, size + 1);
         swingHighs[size] = high[i];
         swingHighTimes[size] = time[i];
      }

      //--- Check swing low
      if(IsSwingLow(low, i, InpLookback))
      {
         int size = ArraySize(swingLows);
         ArrayResize(swingLows, size + 1);
         ArrayResize(swingLowTimes, size + 1);
         swingLows[size] = low[i];
         swingLowTimes[size] = time[i];
      }
   }

   //--- Filter broken levels if enabled
   double currentClose = close[0];

   //--- Collect all valid levels (most recent first, already sorted since i starts from newest)
   double   levels[];
   datetime levelTimes[];
   color    levelColors[];
   string   levelTypes[];

   ArrayResize(levels, 0);
   ArrayResize(levelTimes, 0);
   ArrayResize(levelColors, 0);
   ArrayResize(levelTypes, 0);

   //--- Add swing highs (filter broken)
   for(int i = 0; i < ArraySize(swingHighs); i++)
   {
      if(InpRemoveBroken && currentClose > swingHighs[i])
         continue;  // Price closed above this high — level broken

      int size = ArraySize(levels);
      ArrayResize(levels, size + 1);
      ArrayResize(levelTimes, size + 1);
      ArrayResize(levelColors, size + 1);
      ArrayResize(levelTypes, size + 1);
      levels[size] = swingHighs[i];
      levelTimes[size] = swingHighTimes[i];
      levelColors[size] = InpHighColor;
      levelTypes[size] = "H";
   }

   //--- Add swing lows (filter broken)
   for(int i = 0; i < ArraySize(swingLows); i++)
   {
      if(InpRemoveBroken && currentClose < swingLows[i])
         continue;  // Price closed below this low — level broken

      int size = ArraySize(levels);
      ArrayResize(levels, size + 1);
      ArrayResize(levelTimes, size + 1);
      ArrayResize(levelColors, size + 1);
      ArrayResize(levelTypes, size + 1);
      levels[size] = swingLows[i];
      levelTimes[size] = swingLowTimes[i];
      levelColors[size] = InpLowColor;
      levelTypes[size] = "L";
   }

   //--- Limit to max lines (keep the most recent ones)
   int totalLevels = ArraySize(levels);
   int startIdx = 0;
   if(totalLevels > InpMaxLines)
      startIdx = totalLevels - InpMaxLines;

   //--- Draw lines and labels
   int drawn = 0;
   for(int i = startIdx; i < totalLevels; i++)
   {
      string lineName  = g_prefix + "L" + IntegerToString(drawn);
      string labelName = g_prefix + "P" + IntegerToString(drawn);

      DrawLevel(lineName, labelName, levelTimes[i], levels[i], levelColors[i], levelTypes[i]);
      drawn++;
   }

   return rates_total;
}

//+------------------------------------------------------------------+
//| Check if bar at index is a swing high                              |
//+------------------------------------------------------------------+
bool IsSwingHigh(const double &high[], int index, int lookback)
{
   double val = high[index];
   for(int i = 1; i <= lookback; i++)
   {
      if(high[index + i] >= val) return false;  // Left side (older)
      if(high[index - i] >= val) return false;  // Right side (newer)
   }
   return true;
}

//+------------------------------------------------------------------+
//| Check if bar at index is a swing low                               |
//+------------------------------------------------------------------+
bool IsSwingLow(const double &low[], int index, int lookback)
{
   double val = low[index];
   for(int i = 1; i <= lookback; i++)
   {
      if(low[index + i] <= val) return false;  // Left side (older)
      if(low[index - i] <= val) return false;  // Right side (newer)
   }
   return true;
}

//+------------------------------------------------------------------+
//| Draw a horizontal level line + price label                         |
//+------------------------------------------------------------------+
void DrawLevel(string lineName, string labelName, datetime startTime,
               double price, color clr, string type)
{
   //--- Create trend line from swing point to far right
   ObjectCreate(0, lineName, OBJ_TREND, 0, startTime, price, startTime + PeriodSeconds() * 10000, price);
   ObjectSetInteger(0, lineName, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, lineName, OBJPROP_STYLE, STYLE_SOLID);
   ObjectSetInteger(0, lineName, OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, lineName, OBJPROP_RAY_RIGHT, true);
   ObjectSetInteger(0, lineName, OBJPROP_RAY_LEFT, false);
   ObjectSetInteger(0, lineName, OBJPROP_BACK, true);       // Draw behind candles
   ObjectSetInteger(0, lineName, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, lineName, OBJPROP_HIDDEN, true);     // Hide from object list

   //--- Price label at right edge
   ObjectCreate(0, labelName, OBJ_ARROW_RIGHT_PRICE, 0, TimeCurrent(), price);
   ObjectSetInteger(0, labelName, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, labelName, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, labelName, OBJPROP_HIDDEN, true);
}
//+------------------------------------------------------------------+
